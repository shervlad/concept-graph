# Chunk 11: Generate Node Description on Click

## Goal

When a user clicks a node that has no description, call the LLM to generate a one-sentence description and persist it.

**Prerequisite:** Chunks 7–10

## Changes

### `generator.py` — Add `describe_node` function

```python
async def describe_node(name: str, year: int | None, backend: str = "openai") -> str:
    year_str = f" ({year})" if year else ""
    prompt = f'Describe "{name}"{year_str} in one sentence. Output ONLY the sentence, nothing else.'
    cfg = BACKENDS[backend]
    # call LLM with max_tokens=100, return raw text (not JSON)
```

This is a plain text call, not JSON — just returns the sentence string directly. No parsing needed.

### `server.py` — Add `POST /api/describe` endpoint

```python
async def handle_describe(request):
    body = await request.json()
    node_id = body.get("id")
    backend = body.get("backend", "openai")
    
    db = request.app["db"]
    # Look up node name and year from DB
    # Call describe_node(name, year, backend)
    # Update node desc in DB
    # Return {"id": node_id, "desc": description}
```

**`db.py`** — Add `update_desc(node_id, desc)` method:
```python
def update_desc(self, node_id: str, desc: str):
    with self._lock:
        self._conn.execute("UPDATE nodes SET desc=? WHERE id=?", (desc, node_id))
        self._conn.commit()
```

Also add `get_node(node_id)` if it doesn't exist, to fetch name and year.

Register route: `app.router.add_post("/api/describe", handle_describe)`

### `index.html` — Trigger description on click

In the existing node click/tooltip handler:

```javascript
async function ensureDescription(node) {
  if (node.desc) return node.desc;
  const backend = document.getElementById('backend-select').value;
  const resp = await fetch('/api/describe', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: node.id, backend})
  });
  if (resp.ok) {
    const data = await resp.json();
    node.desc = data.desc;
  }
  return node.desc || '';
}
```

Call `ensureDescription(node)` when showing the tooltip. Show a "Loading..." placeholder while the call is in flight.

## Verification

```bash
cd ~/projects/knowledge-tree
python server.py &
curl -X POST http://localhost:8080/api/import -H "Content-Type: application/json" -d @seed_data.json
# Open http://localhost:8080
# Click a node → tooltip shows "Loading..." then the generated description
# Click same node again → description appears instantly (cached in DB)
```
