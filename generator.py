import json
import os
import asyncio
import logging
import re
from collections import deque

log = logging.getLogger("generator")

BACKENDS = {
    "openai":   {"sdk": "openai",    "model": "gpt-4o",              "env": "OPENAI_API_KEY"},
    "claude":   {"sdk": "anthropic", "model": "claude-sonnet-4-20250514", "env": "ANTHROPIC_API_KEY"},
    "gemini":   {"sdk": "gemini",    "model": "gemini-2.0-flash",    "env": "GOOGLE_API_KEY"},
    "deepseek": {"sdk": "openai",    "model": "deepseek-v4-pro",     "env": "DEEPSEEK_API_KEY",
                 "base_url": "https://api.deepseek.com/v1"},
    "grok":     {"sdk": "openai",    "model": "grok-3",              "env": "XAI_API_KEY",
                 "base_url": "https://api.x.ai/v1"},
}

SYSTEM_PROMPT = "You list related knowledge concepts. Output ONLY valid JSON."


def _build_user_prompt(focal: str, existing_names: list[str], count: int = 20) -> str:
    skip = ", ".join(f'"{n}"' for n in existing_names[:200])
    return (
        f'List {count} concepts related to "{focal}".\n'
        f'For each return: name, year (integer, negative=BC), weight (0.0-1.0 relatedness to "{focal}").\n'
        f'Skip these: [{skip}]\n'
        f'Output: {{"concepts": [{{"name": "...", "year": ..., "weight": ...}}, ...]}}'
    )


def _make_id(name: str, existing_ids: set) -> str:
    base = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    if not base:
        base = "concept"
    candidate = base
    n = 2
    while candidate in existing_ids:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise


async def call_llm(prompt: str, backend: str = "openai", system_prompt: str = None) -> dict:
    cfg = BACKENDS[backend]
    env_key = os.environ.get(cfg["env"], "")
    if not env_key:
        raise ValueError(f"Missing {cfg['env']} environment variable for backend '{backend}'")

    sys_msg = system_prompt or SYSTEM_PROMPT

    if cfg["sdk"] == "openai":
        from openai import AsyncOpenAI
        kwargs = {"api_key": env_key}
        if "base_url" in cfg:
            kwargs["base_url"] = cfg["base_url"]
        client = AsyncOpenAI(**kwargs)
        resp = await client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=2048,
        )
        content = resp.choices[0].message.content
        if not content:
            content = getattr(resp.choices[0].message, 'reasoning_content', '') or ''
        return _parse_json_response(content)

    elif cfg["sdk"] == "anthropic":
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=env_key)
        resp = await client.messages.create(
            model=cfg["model"],
            max_tokens=2048,
            system=sys_msg + "\nRespond with ONLY valid JSON.",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        return _parse_json_response(resp.content[0].text)

    elif cfg["sdk"] == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=env_key)
        model = genai.GenerativeModel(cfg["model"])
        resp = await asyncio.to_thread(
            model.generate_content,
            sys_msg + "\n\n" + prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.7,
                max_output_tokens=2048,
            ),
        )
        return _parse_json_response(resp.text)


async def expand_once(focal: str, db, backend: str = "openai", count: int = 20, system_prompt: str = None) -> dict:
    existing_ids = set(db.get_node_ids())
    existing_names = list(db.get_node_names_by_ids(list(existing_ids)).values())
    focal_name = db.get_node_names_by_ids([focal]).get(focal, focal)
    prompt = _build_user_prompt(focal_name, existing_names, count)

    log.info(f"Expanding '{focal}' via {backend} (asking for {count} concepts)")
    result = await call_llm(prompt, backend, system_prompt=system_prompt)

    new_nodes = []
    new_edges = []

    for concept in result.get("concepts", []):
        name = concept.get("name")
        if not name:
            continue
        nid = _make_id(name, existing_ids)
        year = concept.get("year") if isinstance(concept.get("year"), int) else None
        weight = max(0.0, min(1.0, float(concept.get("weight", 0.5))))

        node = {"id": nid, "name": name, "year": year, "desc": ""}
        if db.add_node(node):
            new_nodes.append(node)
            existing_ids.add(nid)
            edge = {"source": focal, "target": nid, "weight": weight}
            if db.add_edge(edge):
                new_edges.append(edge)

    log.info(f"Added {len(new_nodes)} nodes, {len(new_edges)} edges")
    return {"nodes": new_nodes, "edges": new_edges}


async def _notify(callback, event: str, data: dict):
    if callback:
        try:
            await callback(event, data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"Callback error on '{event}': {e}")


async def expansion_loop(seed, db, backend="openai", callback=None, max_rounds=0, count=20, system_prompt=None):
    queue = deque([seed])
    visited = set()
    round_num = 0

    try:
        while queue:
            if max_rounds and round_num >= max_rounds:
                break
            focal = queue.popleft()
            if focal in visited:
                continue
            visited.add(focal)

            try:
                result = await expand_once(focal, db, backend, count, system_prompt=system_prompt)
                new_nodes = result["nodes"]
                new_edges = result["edges"]
                for n in new_nodes:
                    queue.append(n["id"])
                if callback:
                    stats = db.get_stats()
                    await callback("expand", {
                        "round": round_num,
                        "focal": focal,
                        "new_nodes": new_nodes,
                        "new_edges": new_edges,
                        "stats": stats,
                    })
            except asyncio.CancelledError:
                if callback:
                    await callback("stopped", {"stats": db.get_stats()})
                return
            except Exception as e:
                if callback:
                    await callback("error", {"round": round_num, "focal": focal, "error": str(e), "stats": db.get_stats()})
            round_num += 1

        if callback:
            await callback("done", {"stats": db.get_stats()})
    except asyncio.CancelledError:
        if callback:
            await callback("stopped", {"round": round_num, "stats": db.get_stats()})


async def describe_node(name: str, year: int | None, backend: str = "openai", system_prompt: str = None) -> str:
    year_str = f" ({year})" if year else ""
    prompt = f'Describe "{name}"{year_str} in one sentence. Output ONLY the sentence, nothing else.'
    cfg = BACKENDS.get(backend)
    if not cfg:
        raise ValueError(f"Unknown backend: {backend}")
    key = os.environ.get(cfg["env"], "")
    if not key:
        raise RuntimeError(f"Missing API key: {cfg['env']}")

    sys_msg = system_prompt if system_prompt else "You are a concise encyclopedia."

    if cfg["sdk"] == "openai":
        from openai import AsyncOpenAI
        client_kwargs = {"api_key": key}
        if "base_url" in cfg:
            client_kwargs["base_url"] = cfg["base_url"]
        client = AsyncOpenAI(**client_kwargs)
        resp = await client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
        )
        content = resp.choices[0].message.content or ""
        return content.strip()

    elif cfg["sdk"] == "anthropic":
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=key)
        resp = await client.messages.create(
            model=cfg["model"],
            max_tokens=100,
            system=sys_msg,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()

    elif cfg["sdk"] == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel(cfg["model"])
        resp = await asyncio.to_thread(
            model.generate_content,
            sys_msg + "\n\n" + prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=100,
            ),
        )
        return resp.text.strip()

    raise ValueError(f"Unknown SDK: {cfg['sdk']}")


async def name_clusters(clusters: list[dict], backend: str = "openai", system_prompt: str = None) -> list[str]:
    prompt = """Given these clusters of knowledge concepts, provide a short descriptive name (2-4 words) for each cluster.

Clusters:
"""
    for i, c in enumerate(clusters):
        names = ", ".join(c.get("names", [])[:15])
        prompt += f"\n{i+1}. [{names}]"

    prompt += f"""

Respond with ONLY this JSON:
{{"names": ["Cluster 1 Name", "Cluster 2 Name", ...]}}"""

    result = await call_llm(prompt, backend, system_prompt=system_prompt)
    return result.get("names", [f"Cluster {i+1}" for i in range(len(clusters))])
