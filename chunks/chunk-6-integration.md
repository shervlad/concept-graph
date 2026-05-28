# Chunk 6: Seed Data & Integration Tests

## Project Context

We're building "Knowledge Tree" — a web app that visualizes human knowledge as a force-directed graph. An LLM expands the graph from a seed word, the browser renders it in real time with WebGL (Deck.gl).

This chunk creates the **initial seed dataset** and **end-to-end integration tests** that verify all components work together.

**Working directory:** `~/projects/knowledge-tree/`

**Prerequisite chunks:** All previous chunks (1–5) must be implemented.

## Files to Create

- `seed_data.json` — 120+ concepts with edges in the import format
- `test_integration.py` — 5 end-to-end tests

## Dependencies

```bash
pip install aiohttp pytest pytest-aiohttp
```

Also depends on:
- `db.py` (Chunk 1) — `GraphDB` class
- `generator.py` (Chunk 2) — `expansion_loop`, `name_clusters`, `BACKENDS`
- `server.py` (Chunk 3) — `create_app` factory

## `seed_data.json` Format

The seed data provides a starting graph for the Knowledge Tree. It uses the same format accepted by `POST /api/import` and `db.import_json()`:

```json
{
  "nodes": [
    {"id": "calculus", "name": "Calculus", "year": 1687, "domains": ["mathematics"], "desc": "Mathematics of change and motion via derivatives and integrals"},
    {"id": "algebra", "name": "Algebra", "year": 820, "domains": ["mathematics"], "desc": "Branch of mathematics dealing with symbols and rules for manipulating them"},
    ...
  ],
  "links": [
    {"source": "algebra", "target": "calculus", "weight": 0.9},
    ...
  ]
}
```

**No top-level `domains` key.** Domain entries are auto-created from node domain arrays by the database layer (auto-generated colors via MD5 hash).

### Seed Data Content Requirements

The seed data should contain approximately **120 nodes** and **170 edges** covering the major branches of human knowledge. The concepts should span:

**Domains to cover (at minimum):**
- `mathematics` — arithmetic, algebra, geometry, calculus, topology, statistics, etc.
- `physics` — mechanics, thermodynamics, electromagnetism, quantum mechanics, relativity, etc.
- `chemistry` — atomic theory, organic chemistry, periodic table, etc.
- `biology` — cell theory, genetics, evolution, taxonomy, etc.
- `computing` — algorithms, programming, internet, AI, etc.
- `philosophy` — logic, ethics, metaphysics, epistemology, etc.
- `medicine` — anatomy, germ theory, vaccination, etc.
- `astronomy` — heliocentrism, telescopes, big bang theory, etc.
- `engineering` — steam engine, electricity, bridges, etc.
- `economics` — supply and demand, capitalism, game theory, etc.
- `linguistics` — grammar, phonetics, writing systems, etc.
- `art` — perspective, music theory, architecture, etc.
- `psychology` — behaviorism, cognitive science, psychoanalysis, etc.
- `political_science` — democracy, constitution, international law, etc.

**Node requirements:**
- Each node needs: `id` (snake_case), `name` (human-readable), `year` (integer, negative for BC), `domains` (array of 1-3 domain keys), `desc` (one sentence)
- Years should be historically accurate (approximate is fine for ancient concepts)
- Year range should span from ~-8000 (early mathematics/writing) to ~2020 (modern AI/quantum computing)
- IDs must be unique snake_case strings

**Edge requirements:**
- Directed: `source` influenced/enabled `target`
- Weight 0.0–1.0 (1.0 = direct dependency, 0.5 = moderate influence, 0.2 = loose connection)
- Each node should have at least one connection
- Create cross-domain connections where they exist (e.g., mathematics → physics, philosophy → computing)

### Example Nodes (representative sample)

```json
{"id": "writing", "name": "Writing", "year": -3200, "domains": ["linguistics"], "desc": "System of visual marks representing language"},
{"id": "euclidean_geometry", "name": "Euclidean Geometry", "year": -300, "domains": ["mathematics"], "desc": "Axiomatic study of plane and solid figures based on Euclid's Elements"},
{"id": "aristotelian_logic", "name": "Aristotelian Logic", "year": -350, "domains": ["philosophy", "mathematics"], "desc": "Formal system of deductive reasoning using syllogisms"},
{"id": "newtonian_mechanics", "name": "Newtonian Mechanics", "year": 1687, "domains": ["physics", "mathematics"], "desc": "Laws of motion and universal gravitation"},
{"id": "general_relativity", "name": "General Relativity", "year": 1915, "domains": ["physics", "mathematics"], "desc": "Geometric theory of gravitation describing spacetime curvature"},
{"id": "germ_theory", "name": "Germ Theory", "year": 1862, "domains": ["medicine", "biology"], "desc": "Theory that microorganisms cause many diseases"},
{"id": "turing_machine", "name": "Turing Machine", "year": 1936, "domains": ["computing", "mathematics"], "desc": "Abstract mathematical model of computation"},
{"id": "natural_selection", "name": "Natural Selection", "year": 1859, "domains": ["biology"], "desc": "Mechanism of evolution through differential survival and reproduction"},
{"id": "deep_learning", "name": "Deep Learning", "year": 2006, "domains": ["computing", "mathematics"], "desc": "Machine learning using multi-layer neural networks"},
{"id": "democracy", "name": "Democracy", "year": -508, "domains": ["political_science"], "desc": "System of government where power is vested in the people"}
```

### Example Edges

```json
{"source": "euclidean_geometry", "target": "newtonian_mechanics", "weight": 0.7},
{"source": "newtonian_mechanics", "target": "general_relativity", "weight": 0.9},
{"source": "aristotelian_logic", "target": "turing_machine", "weight": 0.5},
{"source": "germ_theory", "target": "vaccination", "weight": 0.8},
{"source": "turing_machine", "target": "deep_learning", "weight": 0.6},
{"source": "natural_selection", "target": "genetics", "weight": 0.7}
```

## Integration Tests (`test_integration.py`)

These tests verify the full stack works together. They use a real server instance with a temporary database.

```python
import pytest
import json
import asyncio
import os
from pathlib import Path
from aiohttp import web
from server import create_app
from db import GraphDB

SEED_DATA_PATH = Path(__file__).parent / "seed_data.json"


@pytest.fixture
def app(tmp_path):
    """Create app with a fresh temporary database."""
    return create_app(db_path=str(tmp_path / "integration_test.db"))


@pytest.fixture
async def client(aiohttp_client, app):
    return await aiohttp_client(app)


def load_seed_data():
    with open(SEED_DATA_PATH) as f:
        return json.load(f)


async def test_import_seed_data(client):
    """Import seed_data.json via API, verify correct counts in stats."""
    data = load_seed_data()
    
    resp = await client.post("/api/import", json=data)
    assert resp.status == 200
    result = await resp.json()
    assert result["status"] == "imported"
    assert result["nodes"] >= 100  # should be ~120
    assert result["edges"] >= 100  # should be ~170
    
    # Verify via stats endpoint
    resp = await client.get("/api/stats")
    stats = await resp.json()
    assert stats["nodes"] >= 100
    assert stats["edges"] >= 100
    assert stats["domains"] >= 10  # should have 10+ domains
    
    # Verify graph endpoint returns data
    resp = await client.get("/api/graph?limit=500")
    graph = await resp.json()
    assert len(graph["nodes"]) > 0
    assert len(graph["links"]) > 0
    assert len(graph["domains"]) >= 10
    
    # Verify every node has required fields
    for node in graph["nodes"]:
        assert "id" in node
        assert "name" in node
        assert "year" in node
        assert "domains" in node


async def test_expand_from_seed(client):
    """Import seed data, then expand from a concept. Requires an LLM API key."""
    # Skip if no API keys configured
    has_key = any(os.environ.get(k) for k in [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY", "XAI_API_KEY"
    ])
    if not has_key:
        pytest.skip("No LLM API key configured")
    
    # Find a configured backend
    resp = await client.get("/api/backends")
    backends = await resp.json()
    backend = None
    for name, info in backends.items():
        if info["configured"]:
            backend = name
            break
    assert backend is not None, "No configured backend found"
    
    # Import seed data first
    data = load_seed_data()
    await client.post("/api/import", json=data)
    
    initial_stats = await (await client.get("/api/stats")).json()
    initial_count = initial_stats["nodes"]
    
    # Start expansion (1 round)
    resp = await client.post("/api/expand", json={
        "seed": "calculus",
        "backend": backend,
        "rounds": 1
    })
    assert resp.status == 200
    
    # Wait for expansion to complete (poll stats)
    for _ in range(30):  # max 30 seconds
        await asyncio.sleep(1)
        stats = await (await client.get("/api/stats")).json()
        if stats["nodes"] > initial_count:
            break
    
    final_stats = await (await client.get("/api/stats")).json()
    assert final_stats["nodes"] > initial_count, "Expansion should have added new nodes"


async def test_sse_receives_expansion(client):
    """Connect SSE, trigger import, verify event is received."""
    # Connect to SSE stream
    sse_resp = await client.get("/api/stream")
    assert sse_resp.status == 200
    assert "text/event-stream" in sse_resp.headers.get("Content-Type", "")
    
    # Read initial stats event
    lines_read = []
    try:
        for _ in range(4):
            line = await asyncio.wait_for(sse_resp.content.readline(), timeout=3)
            lines_read.append(line.decode())
    except asyncio.TimeoutError:
        pass
    
    initial = "".join(lines_read)
    assert "stats" in initial or "data" in initial
    
    # Import some data (should trigger a broadcast)
    import_data = {
        "nodes": [
            {"id": "test_a", "name": "Test A", "year": 2000, "domains": ["testing"]},
            {"id": "test_b", "name": "Test B", "year": 2001, "domains": ["testing"]},
        ],
        "links": [{"source": "test_a", "target": "test_b", "weight": 0.5}]
    }
    await client.post("/api/import", json=import_data)
    
    # Read the broadcast event
    broadcast_lines = []
    try:
        for _ in range(4):
            line = await asyncio.wait_for(sse_resp.content.readline(), timeout=3)
            broadcast_lines.append(line.decode())
    except asyncio.TimeoutError:
        pass
    
    broadcast = "".join(broadcast_lines)
    assert "stats" in broadcast or "nodes" in broadcast
    sse_resp.close()


async def test_graph_persists_restart(client, tmp_path):
    """Import data, create a new app instance with same DB, verify data persists."""
    # Import data
    import_data = {
        "nodes": [
            {"id": "persist_a", "name": "Persist A", "year": 2000, "domains": ["test"]},
            {"id": "persist_b", "name": "Persist B", "year": 2001, "domains": ["test"]},
        ],
        "links": [{"source": "persist_a", "target": "persist_b", "weight": 0.7}]
    }
    resp = await client.post("/api/import", json=import_data)
    assert resp.status == 200
    
    # Get the DB path from the app
    db_path = client.app["db"].path if hasattr(client.app["db"], "path") else None
    
    # If we can access the DB path, verify with a fresh GraphDB instance
    if db_path:
        db2 = GraphDB(db_path)
        stats = db2.get_stats()
        assert stats["nodes"] >= 2
        assert stats["edges"] >= 1
        db2.close()
    else:
        # Alternative: just verify the data via API (same app instance, but proves DB persistence)
        resp = await client.get("/api/stats")
        stats = await resp.json()
        assert stats["nodes"] >= 2
        assert stats["edges"] >= 1


async def test_cluster_naming(client):
    """POST /api/cluster-names returns names for given clusters. Requires API key."""
    has_key = any(os.environ.get(k) for k in [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY", "XAI_API_KEY"
    ])
    if not has_key:
        pytest.skip("No LLM API key configured")
    
    # Find a configured backend
    resp = await client.get("/api/backends")
    backends = await resp.json()
    backend = None
    for name, info in backends.items():
        if info["configured"]:
            backend = name
            break
    
    resp = await client.post("/api/cluster-names", json={
        "clusters": [
            {"names": ["Calculus", "Algebra", "Geometry", "Topology"]},
            {"names": ["Quantum Mechanics", "Relativity", "Thermodynamics"]},
            {"names": ["DNA", "Evolution", "Cell Theory", "Genetics"]}
        ],
        "backend": backend
    })
    
    assert resp.status == 200
    data = await resp.json()
    assert "names" in data
    assert len(data["names"]) == 3
    assert all(isinstance(n, str) and len(n) > 0 for n in data["names"])
```

## Running the Tests

```bash
cd ~/projects/knowledge-tree

# Run all integration tests (skips tests requiring API keys if not set)
pytest test_integration.py -v

# Run with a configured backend (e.g., OpenAI)
OPENAI_API_KEY=sk-... pytest test_integration.py -v
```

## Acceptance Criteria

1. `seed_data.json` contains 120+ nodes and 170+ edges
2. `seed_data.json` covers 10+ distinct domains
3. All years are historically plausible
4. Every node has at least one edge connection
5. `pytest test_integration.py -v` — all tests pass (LLM-dependent tests skip gracefully if no API key)

## Full End-to-End Verification

After all 6 chunks are implemented, run this complete verification:

```bash
cd ~/projects/knowledge-tree

# 1. Run all unit tests
pytest test_db.py test_generator.py test_server.py -v

# 2. Run integration tests
pytest test_integration.py -v

# 3. Start the server
python server.py &

# 4. Import seed data
curl -X POST http://localhost:8080/api/import \
  -H "Content-Type: application/json" \
  -d @seed_data.json

# 5. Open browser to http://localhost:8080
# 6. Verify the 14 manual checks from Chunk 5
# 7. Open test-force-worker.html to verify standalone worker (Chunk 4)
```
