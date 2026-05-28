# Chunk 3: Server

## Project Context

We're building "Knowledge Tree" — a web app that visualizes human knowledge as a force-directed graph. An LLM expands the graph from a seed word, the browser renders it in real time with WebGL.

This chunk implements the **aiohttp web server** that ties together the database (Chunk 1) and generator (Chunk 2), exposes a REST API, and streams live updates to the browser via Server-Sent Events.

**Working directory:** `~/projects/knowledge-tree/`

**Prerequisite chunks:** Chunk 1 (`db.py`) and Chunk 2 (`generator.py`) must be implemented first.

## Files to Create

- `server.py` — aiohttp application
- `test_server.py` — 13 pytest tests using aiohttp test client

## Dependencies

```bash
pip install aiohttp pytest pytest-aiohttp
```

Also depends on:
- `db.py` (Chunk 1) — `GraphDB` class
- `generator.py` (Chunk 2) — `expansion_loop`, `name_clusters`, `BACKENDS`

## API Contract

### Static Files

| Route | Method | Response |
|-------|--------|----------|
| `GET /` | GET | Serve `index.html` from project directory |
| `GET /<file>` | GET | Serve static files (*.html, *.js, *.css, *.json) from project directory |

### REST Endpoints

#### `GET /api/graph`

Query the knowledge graph.

**Query params:**
- `limit` (int, default 500) — max nodes to return
- `center` (string, optional) — node ID to center BFS around

**Response 200:**
```json
{
  "domains": {"physics": {"color": "#ff8a65", "label": "Physics"}, ...},
  "nodes": [{"id": "...", "name": "...", "year": 1687, "domains": ["mathematics"], "desc": "..."}, ...],
  "links": [{"source": "a", "target": "b", "weight": 0.8}, ...],
  "total_nodes": 12340,
  "total_edges": 34500
}
```

#### `GET /api/stats`

**Response 200:**
```json
{"nodes": 12340, "edges": 34500, "domains": 14}
```

#### `GET /api/backends`

List configured LLM backends.

**Response 200:**
```json
{
  "openai":   {"model": "gpt-4o",              "configured": true},
  "claude":   {"model": "claude-sonnet-4-20250514", "configured": false},
  "gemini":   {"model": "gemini-2.0-flash",    "configured": true},
  "deepseek": {"model": "deepseek-chat",       "configured": false},
  "grok":     {"model": "grok-3",              "configured": false}
}
```

`configured` is true if the env var for that backend is set.

#### `POST /api/expand`

Start graph expansion in the background.

**Request body:**
```json
{"seed": "mathematics", "backend": "openai", "rounds": 5}
```
- `seed` (string, required) — concept to expand from
- `backend` (string, default "openai") — LLM backend to use
- `rounds` (int, default 0) — 0 = unlimited, else stop after N rounds

**Response 200:**
```json
{"status": "started", "seed": "mathematics", "backend": "openai"}
```

**Error responses:**
- 400: `{"error": "Missing 'seed' parameter"}` — empty or missing seed
- 400: `{"error": "Unknown backend: xyz"}` — invalid backend name
- 400: `{"error": "Missing OPENAI_API_KEY environment variable"}` — backend not configured
- 409: `{"error": "Expansion already running. Stop it first."}` — another expansion is active

Only one expansion task runs at a time. The task runs as an `asyncio.Task` in the background.

#### `POST /api/stop`

Stop the running expansion.

**Response 200:**
```json
{"status": "stopping"}    // if expansion was running
{"status": "not running"} // if nothing was running
```

#### `POST /api/cluster-names`

Ask an LLM to name spatial clusters.

**Request body:**
```json
{
  "clusters": [
    {"names": ["Calculus", "Algebra", "Geometry"]},
    {"names": ["Quantum Mechanics", "Relativity"]}
  ],
  "backend": "openai"
}
```

**Response 200:**
```json
{"names": ["Classical Mathematics", "Modern Physics"]}
```

**Error 500:** `{"error": "..."}` if LLM call fails.

#### `POST /api/import`

Bulk import graph data.

**Request body:** Same format as `GET /api/graph` response (or legacy format with single `domain` field per node).

**Response 200:**
```json
{"status": "imported", "nodes": 120, "edges": 170, "domains": 14}
```

Side effect: broadcasts a `stats` SSE event to all connected clients.

### SSE Endpoint

#### `GET /api/stream`

Server-Sent Events stream. Keeps the connection open and pushes events as they occur.

**Response headers:**
```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

**Events pushed:**

```
event: stats
data: {"nodes": 120, "edges": 170, "domains": 14}
```
Sent immediately on connect, and after imports.

```
event: expand
data: {"round": 3, "focal": "calculus", "new_nodes": [...], "new_edges": [...], "stats": {...}}
```
Sent after each expansion round completes.

```
event: status
data: {"message": "Expanding around 'thermodynamics'..."}
```
General status updates.

```
event: error
data: {"message": "Rate limited, retrying", "focal": "optics"}
```
Non-fatal errors during expansion.

```
event: done
data: {"stats": {"nodes": 340, "edges": 890, "domains": 12}}
```
Expansion finished.

```
event: stopped
data: {"stats": {...}}
```
Expansion was cancelled by user.

## Architecture

```python
# Module-level state
db = GraphDB(DB_PATH)
sse_clients: list[asyncio.Queue] = []    # one queue per connected SSE client
expansion_task: asyncio.Task | None = None

def broadcast(event: str, data: dict):
    """Push an SSE event to all connected clients. Drop clients whose queues are full."""

async def sse_callback(event: str, data: dict):
    """Passed to expansion_loop as the callback. Just calls broadcast()."""
```

**SSE implementation:** Use `aiohttp.web.StreamResponse`. On connect, create an `asyncio.Queue`, add it to `sse_clients`. In a loop, `await queue.get()` and write to the response. On disconnect (ConnectionResetError, CancelledError), remove the queue.

**Expansion management:** `POST /api/expand` creates an `asyncio.Task` running `expansion_loop(...)`. Only one can be active. `POST /api/stop` cancels it.

**Configuration via env vars:**
- `KNOWLEDGE_DB` — SQLite path (default: `knowledge.db`)
- `PORT` — server port (default: `8080`)

## Tests (`test_server.py`)

Use `pytest-aiohttp` for testing with aiohttp's test client. Each test gets a fresh database.

```python
import pytest
import json
import asyncio
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

# For pytest-aiohttp style:
from server import create_app  # We need a factory function (see implementation note below)

@pytest.fixture
def app(tmp_path):
    """Create app with a fresh temporary database."""
    return create_app(db_path=str(tmp_path / "test.db"))

@pytest.fixture
def client(aiohttp_client, app):
    return aiohttp_client(app)


async def test_index_served(client):
    """GET / returns 200 with HTML content."""
    resp = await (await client).get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "html" in text.lower()

async def test_graph_empty(client):
    """GET /api/graph on empty db returns empty arrays."""
    resp = await (await client).get("/api/graph")
    assert resp.status == 200
    data = await resp.json()
    assert data["nodes"] == []
    assert data["links"] == []
    assert data["total_nodes"] == 0

async def test_graph_with_data(client):
    """Import data, then GET /api/graph returns it."""
    c = await client
    import_data = {
        "nodes": [
            {"id": "a", "name": "A", "year": 2000, "domains": ["math"]},
            {"id": "b", "name": "B", "year": 2001, "domains": ["math"]},
        ],
        "links": [{"source": "a", "target": "b", "weight": 0.8}]
    }
    await c.post("/api/import", json=import_data)
    resp = await c.get("/api/graph")
    data = await resp.json()
    assert len(data["nodes"]) == 2
    assert len(data["links"]) == 1

async def test_graph_limit(client):
    """Import 20 nodes, GET /api/graph?limit=5 returns at most 5."""
    c = await client
    import_data = {
        "nodes": [{"id": f"n{i}", "name": f"N{i}", "year": 2000, "domains": []} for i in range(20)],
        "links": []
    }
    await c.post("/api/import", json=import_data)
    resp = await c.get("/api/graph?limit=5")
    data = await resp.json()
    assert len(data["nodes"]) <= 5
    assert data["total_nodes"] == 20

async def test_stats(client):
    """GET /api/stats returns correct counts."""
    c = await client
    import_data = {
        "nodes": [
            {"id": "a", "name": "A", "year": 2000, "domains": ["x"]},
            {"id": "b", "name": "B", "year": 2001, "domains": ["x"]},
        ],
        "links": [{"source": "a", "target": "b", "weight": 0.5}]
    }
    await c.post("/api/import", json=import_data)
    resp = await c.get("/api/stats")
    data = await resp.json()
    assert data["nodes"] == 2
    assert data["edges"] == 1

async def test_backends(client):
    """GET /api/backends lists all 5 backends with configured status."""
    c = await client
    resp = await c.get("/api/backends")
    data = await resp.json()
    assert set(data.keys()) == {"openai", "claude", "gemini", "deepseek", "grok"}
    for name, info in data.items():
        assert "model" in info
        assert "configured" in info
        assert isinstance(info["configured"], bool)

async def test_import(client):
    """POST /api/import creates nodes accessible via GET /api/graph."""
    c = await client
    import_data = {
        "nodes": [{"id": "x", "name": "X", "year": 2000, "domains": ["test"]}],
        "links": []
    }
    resp = await c.post("/api/import", json=import_data)
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "imported"
    assert body["nodes"] == 1

async def test_expand_no_seed(client):
    """POST /api/expand with empty seed returns 400."""
    c = await client
    resp = await c.post("/api/expand", json={"seed": "", "backend": "openai"})
    assert resp.status == 400

async def test_expand_bad_backend(client):
    """POST /api/expand with unknown backend returns 400."""
    c = await client
    resp = await c.post("/api/expand", json={"seed": "test", "backend": "nonexistent"})
    assert resp.status == 400

async def test_expand_no_api_key(client):
    """POST /api/expand with unconfigured backend returns 400."""
    c = await client
    # Ensure no API keys are set (at least for a backend we know is unconfigured)
    resp = await c.post("/api/expand", json={"seed": "test", "backend": "grok"})
    # If XAI_API_KEY isn't set, this should return 400
    if resp.status == 400:
        data = await resp.json()
        assert "XAI_API_KEY" in data.get("error", "") or "Missing" in data.get("error", "")

async def test_stop_when_not_running(client):
    """POST /api/stop when nothing is running returns 'not running'."""
    c = await client
    resp = await c.post("/api/stop")
    data = await resp.json()
    assert data["status"] == "not running"

async def test_sse_connects(client):
    """GET /api/stream returns 200 with text/event-stream content type."""
    c = await client
    resp = await c.get("/api/stream")
    assert resp.status == 200
    assert "text/event-stream" in resp.headers.get("Content-Type", "")
    # Read the initial stats event
    chunk = await resp.content.readline()
    assert b"event:" in chunk or b"data:" in chunk
    resp.close()

async def test_sse_receives_import_broadcast(client):
    """SSE client receives a stats event when data is imported."""
    c = await client
    # Connect to SSE
    sse_resp = await c.get("/api/stream")
    # Read and discard the initial stats event (4 lines: event, data, empty, empty)
    for _ in range(3):
        await asyncio.wait_for(sse_resp.content.readline(), timeout=2)

    # Import data
    import_data = {
        "nodes": [{"id": "x", "name": "X", "year": 2000, "domains": []}],
        "links": []
    }
    await c.post("/api/import", json=import_data)

    # Read the broadcast event
    lines = []
    try:
        for _ in range(3):
            line = await asyncio.wait_for(sse_resp.content.readline(), timeout=2)
            lines.append(line.decode())
    except asyncio.TimeoutError:
        pass

    full = "".join(lines)
    assert "stats" in full or "nodes" in full
    sse_resp.close()
```

## Implementation Notes

**App factory:** Create a `create_app(db_path=None)` function that returns an `aiohttp.web.Application`. This lets tests pass a temporary DB path:

```python
def create_app(db_path: str = None) -> web.Application:
    if db_path is None:
        db_path = os.environ.get("KNOWLEDGE_DB", "knowledge.db")
    database = GraphDB(db_path)
    app = web.Application()
    app["db"] = database
    app["sse_clients"] = []
    app["expansion_task"] = None
    # ... add routes ...
    return app
```

**Route handlers** access the db via `request.app["db"]`.

**Static file serving:** Use `app.router.add_static("/static/", STATIC_DIR)` for non-root files, and an explicit route for `GET /` → `index.html`. Or use a catch-all that serves files from the project directory, with API routes taking priority.

**Startup logging:** When `server.py` is run directly (`__name__ == "__main__"`), log the port, DB path, graph stats, and which backends are configured.

**SSE format:** Each event is:
```
event: <event_name>\n
data: <json_string>\n
\n
```
The double newline at the end separates events. Use `resp.write(msg.encode())`.

## Acceptance Criteria

```bash
cd ~/projects/knowledge-tree
pytest test_server.py -v
# All 13 tests must pass
```
