import json
import os
import asyncio
import logging
import re

log = logging.getLogger("generator")

BACKENDS = {
    "openai":   {"sdk": "openai",    "model": "gpt-4o",              "env": "OPENAI_API_KEY"},
    "claude":   {"sdk": "anthropic", "model": "claude-sonnet-4-20250514", "env": "ANTHROPIC_API_KEY"},
    "gemini":   {"sdk": "gemini",    "model": "gemini-2.0-flash",    "env": "GOOGLE_API_KEY"},
    "deepseek": {"sdk": "openai",    "model": "deepseek-chat",       "env": "DEEPSEEK_API_KEY",
                 "base_url": "https://api.deepseek.com/v1"},
    "grok":     {"sdk": "openai",    "model": "grok-3",              "env": "XAI_API_KEY",
                 "base_url": "https://api.x.ai/v1"},
}

SYSTEM_PROMPT = """You are a knowledge graph builder creating a comprehensive tree of human knowledge.
You output ONLY valid JSON — no markdown, no commentary, no code fences.
Every concept must have a historically accurate year of first appearance.
Connections must have meaningful weights reflecting intellectual dependency strength."""

def _build_user_prompt(focal: str, existing: dict[str, str], is_seed: bool) -> str:
    if is_seed:
        return f"""Create the foundational concept "{focal}" and 15 closely related concepts that form
its intellectual neighborhood in the tree of human knowledge.

For each concept provide:
- id: unique snake_case (e.g. "calculus", "general_relativity")
- name: human-readable (1-5 words)
- year: integer, year first formulated (negative for BC, e.g. -300)
- domains: array of 1-3 category strings (lowercase_snake_case, e.g. ["physics", "mathematics"])
- desc: one sentence description

Then provide directed edges (source influenced/enabled target):
- source: concept id
- target: concept id
- weight: 0.0-1.0 (1.0 = direct dependency, 0.5 = moderate influence, 0.2 = loose)

Respond with ONLY this JSON structure:
{{"nodes": [...], "edges": [...]}}"""

    existing_list = "\n".join(f"  - {eid}: {ename}" for eid, ename in sorted(existing.items())[:200])
    return f"""Expand the knowledge graph around the concept "{focal}".

EXISTING CONCEPTS (do NOT duplicate — but DO connect to them):
{existing_list}

Generate 15 NEW concepts closely related to "{focal}" that are missing from the graph.
Include concepts that:
- Are prerequisites or foundations for "{focal}"
- Were directly influenced by or built upon "{focal}"
- Are sibling concepts in the same intellectual tradition
- Bridge "{focal}" to other domains

For each new concept:
- id: unique snake_case, not in the existing list above
- name: human-readable (1-5 words)
- year: integer year first formulated (negative for BC)
- domains: array of 1-3 category strings (lowercase_snake_case)
- desc: one sentence description

Then provide edges connecting new concepts to existing ones AND to each other:
- source, target: concept ids (can reference existing or new)
- weight: 0.0-1.0

Respond with ONLY this JSON:
{{"nodes": [...], "edges": [...]}}"""


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


async def call_llm(prompt: str, backend: str = "openai") -> dict:
    cfg = BACKENDS[backend]
    env_key = os.environ.get(cfg["env"], "")
    if not env_key:
        raise ValueError(f"Missing {cfg['env']} environment variable for backend '{backend}'")

    if cfg["sdk"] == "openai":
        from openai import AsyncOpenAI
        kwargs = {"api_key": env_key}
        if "base_url" in cfg:
            kwargs["base_url"] = cfg["base_url"]
        client = AsyncOpenAI(**kwargs)
        resp = await client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=4096,
        )
        return _parse_json_response(resp.choices[0].message.content)

    elif cfg["sdk"] == "anthropic":
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=env_key)
        resp = await client.messages.create(
            model=cfg["model"],
            max_tokens=4096,
            system=SYSTEM_PROMPT + "\nRespond with ONLY valid JSON.",
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
            SYSTEM_PROMPT + "\n\n" + prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.7,
                max_output_tokens=4096,
            ),
        )
        return _parse_json_response(resp.text)


async def expand_once(focal: str, db, backend: str = "openai") -> dict:
    existing_ids = db.get_node_ids()
    existing_names = db.get_node_names_by_ids(list(existing_ids))
    is_seed = len(existing_ids) == 0
    prompt = _build_user_prompt(focal, existing_names, is_seed)

    log.info(f"Calling {backend} to expand around '{focal}' (existing: {len(existing_ids)} concepts)")
    result = await call_llm(prompt, backend)

    new_nodes = []
    new_edges = []

    for node in result.get("nodes", []):
        if not all(k in node for k in ("id", "name")):
            continue
        if node["id"] in existing_ids:
            continue
        if not isinstance(node.get("year"), int):
            node["year"] = None
        if "domains" not in node:
            node["domains"] = ["unknown"]
        if isinstance(node["domains"], str):
            node["domains"] = [node["domains"]]
        node.setdefault("desc", "")
        if db.add_node(node):
            new_nodes.append(node)
            existing_ids.add(node["id"])

    for edge in result.get("edges", []):
        if not all(k in edge for k in ("source", "target")):
            continue
        if edge["source"] not in existing_ids or edge["target"] not in existing_ids:
            continue
        edge["weight"] = max(0.0, min(1.0, float(edge.get("weight", 0.5))))
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


async def expansion_loop(seed: str, db, backend: str = "openai", callback=None, max_rounds: int = 0):
    expanded = set()
    round_num = 0

    try:
        result = await expand_once(seed, db, backend)
        await _notify(callback, "expand", {
            "round": round_num,
            "focal": seed,
            "new_nodes": result["nodes"],
            "new_edges": result["edges"],
            "stats": db.get_stats(),
        })
        expanded.add(seed)
        round_num += 1
    except asyncio.CancelledError:
        log.info("Expansion cancelled")
        await _notify(callback, "stopped", {"stats": db.get_stats()})
        return
    except Exception as e:
        log.error(f"Seed expansion failed: {e}")
        await _notify(callback, "error", {"message": str(e), "focal": seed})
        expanded.add(seed)
        round_num += 1

    while max_rounds == 0 or round_num < max_rounds:
        candidates = db.get_least_expanded(expanded, 5)
        if not candidates:
            log.info("No more candidates to expand")
            await _notify(callback, "done", {"stats": db.get_stats()})
            return

        focal = candidates[0]
        try:
            result = await expand_once(focal, db, backend)
            await _notify(callback, "expand", {
                "round": round_num,
                "focal": focal,
                "new_nodes": result["nodes"],
                "new_edges": result["edges"],
                "stats": db.get_stats(),
            })
            expanded.add(focal)
            round_num += 1
        except asyncio.CancelledError:
            log.info("Expansion cancelled")
            await _notify(callback, "stopped", {"stats": db.get_stats()})
            return
        except Exception as e:
            log.error(f"Expansion round {round_num} failed: {e}")
            await _notify(callback, "error", {"message": str(e), "focal": focal})
            expanded.add(focal)
            round_num += 1

    await _notify(callback, "done", {"stats": db.get_stats()})


async def name_clusters(clusters: list[dict], backend: str = "openai") -> list[str]:
    prompt = """Given these clusters of knowledge concepts, provide a short descriptive name (2-4 words) for each cluster.

Clusters:
"""
    for i, c in enumerate(clusters):
        names = ", ".join(c.get("names", [])[:15])
        prompt += f"\n{i+1}. [{names}]"

    prompt += f"""

Respond with ONLY this JSON:
{{"names": ["Cluster 1 Name", "Cluster 2 Name", ...]}}"""

    result = await call_llm(prompt, backend)
    return result.get("names", [f"Cluster {i+1}" for i in range(len(clusters))])
