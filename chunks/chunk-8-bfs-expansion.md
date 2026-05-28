# Chunk 8: Redesign Expansion as BFS with Simple Prompts

## Goal

Replace the current expansion logic with BFS traversal and minimal LLM prompts. Each call asks for N related concepts (name, year, weight only). IDs are generated server-side, not by the LLM.

**Prerequisite:** Chunk 7 (domains removed)

## Changes

### `generator.py`

**`SYSTEM_PROMPT`** — replace with:
```python
SYSTEM_PROMPT = "You list related knowledge concepts. Output ONLY valid JSON."
```

**`_build_user_prompt(focal, existing_names, count=20)`** — replace both branches with one simple prompt:
```python
def _build_user_prompt(focal: str, existing_names: list[str], count: int = 20) -> str:
    skip = ", ".join(f'"{n}"' for n in existing_names[:200])
    return (
        f'List {count} concepts related to "{focal}".\n'
        f'For each return: name, year (integer, negative=BC), weight (0.0-1.0 relatedness to "{focal}").\n'
        f'Skip these: [{skip}]\n'
        f'Output: {{"concepts": [{{"name": "...", "year": ..., "weight": ...}}, ...]}}'
    )
```

**Add `_make_id(name, existing_ids)`** — new helper:
```python
import re

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
```

**`call_llm`** — two changes:
1. Reduce `max_tokens` to 2048 (minimal output format)
2. After getting response from OpenAI-compatible SDK, handle empty content (DeepSeek thinking mode):
   ```python
   content = resp.choices[0].message.content
   if not content:
       content = getattr(resp.choices[0].message, 'reasoning_content', '') or ''
   return _parse_json_response(content)
   ```

**`expand_once(focal, db, backend, count=20)`** — rewrite:
```python
async def expand_once(focal: str, db, backend: str = "openai", count: int = 20) -> dict:
    existing_ids = db.get_node_ids()
    existing_names = list(db.get_node_names_by_ids(list(existing_ids)).values())
    prompt = _build_user_prompt(focal, existing_names, count)

    log.info(f"Expanding '{focal}' via {backend} (asking for {count} concepts)")
    result = await call_llm(prompt, backend)

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
```

Note: `focal` here is a node ID that must already exist in the DB. The edge goes from `focal → new_concept`.

**`expansion_loop`** — rewrite as BFS:
```python
async def expansion_loop(seed, db, backend="openai", callback=None, max_rounds=0, count=20):
    queue = deque([seed])
    visited = set()
    round_num = 0

    while queue and (max_rounds == 0 or round_num < max_rounds):
        focal = queue.popleft()
        if focal in visited:
            continue
        visited.add(focal)

        try:
            result = await expand_once(focal, db, backend, count)
            await _notify(callback, "expand", {
                "round": round_num,
                "focal": focal,
                "new_nodes": result["nodes"],
                "new_edges": result["edges"],
                "stats": db.get_stats(),
            })
            for node in result["nodes"]:
                queue.append(node["id"])
            round_num += 1
        except asyncio.CancelledError:
            log.info("Expansion cancelled")
            await _notify(callback, "stopped", {"stats": db.get_stats()})
            return
        except Exception as e:
            log.error(f"Expansion round {round_num} failed on '{focal}': {e}")
            await _notify(callback, "error", {"message": str(e), "focal": focal})
            round_num += 1

    await _notify(callback, "done", {"stats": db.get_stats()})
```

Add `from collections import deque` to imports.

**`name_clusters`** — keep unchanged.

### `server.py`

In `handle_expand`, read `count` from request body and pass it through:
```python
count = body.get("count", 20)
# ...
await expansion_loop(seed, db, backend=backend, callback=callback, max_rounds=rounds, count=count)
```

### `test_generator.py`

- **`test_build_seed_prompt` / `test_build_expansion_prompt`**: Replace with a single `test_build_prompt` that checks the new format — verifies `count` appears in prompt, existing names are listed in skip section
- **Add `test_make_id`**: Verify snake_case conversion and collision handling
- **`test_expand_once_deduplicates`**: Mock returns `{"concepts": [{"name": "Existing", "year": 2000, "weight": 0.5}]}` where the generated ID collides with existing — verify it gets a suffixed ID or is skipped
- **`test_expand_once_validates`**: Mock returns concepts with missing `name` — verify they're skipped
- **`test_expand_once_clamps_weight`**: Same logic, but input format is `{"concepts": [...]}`
- **`test_expansion_loop_stops_on_cancel`**: Update for BFS — mock LLM returns some concepts, cancel during second round
- **`test_expansion_loop_continues_on_error`**: First call raises, second call succeeds — verify BFS continues

## Verification

```bash
cd ~/projects/knowledge-tree
pytest test_generator.py -v
DEEPSEEK_API_KEY="..." pytest test_integration.py::test_expand_from_seed -v
```
