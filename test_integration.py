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
    return create_app(db_path=str(tmp_path / "integration_test.db"))


@pytest.fixture
async def client(aiohttp_client, app):
    return await aiohttp_client(app)


def load_seed_data():
    with open(SEED_DATA_PATH) as f:
        return json.load(f)


async def test_import_seed_data(client):
    data = load_seed_data()

    resp = await client.post("/api/import", json=data)
    assert resp.status == 200
    result = await resp.json()
    assert result["status"] == "imported"
    assert result["nodes"] >= 100
    assert result["edges"] >= 100

    resp = await client.get("/api/stats")
    stats = await resp.json()
    assert stats["nodes"] >= 100
    assert stats["edges"] >= 100

    resp = await client.get("/api/graph?limit=500")
    graph = await resp.json()
    assert len(graph["nodes"]) > 0
    assert len(graph["links"]) > 0

    for node in graph["nodes"]:
        assert "id" in node
        assert "name" in node
        assert "year" in node


async def test_expand_from_seed(client):
    has_key = any(os.environ.get(k) for k in [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY", "XAI_API_KEY"
    ])
    if not has_key:
        pytest.skip("No LLM API key configured")

    resp = await client.get("/api/backends")
    backends = await resp.json()
    backend = None
    for name, info in backends.items():
        if info["configured"]:
            backend = name
            break
    assert backend is not None, "No configured backend found"

    data = load_seed_data()
    await client.post("/api/import", json=data)

    initial_stats = await (await client.get("/api/stats")).json()
    initial_count = initial_stats["nodes"]

    resp = await client.post("/api/expand", json={
        "seed": "photography",
        "backend": backend,
        "rounds": 1
    })
    assert resp.status == 200

    for _ in range(60):
        await asyncio.sleep(1)
        stats = await (await client.get("/api/stats")).json()
        if stats["nodes"] > initial_count:
            break

    final_stats = await (await client.get("/api/stats")).json()
    assert final_stats["nodes"] > initial_count, "Expansion should have added new nodes"


async def test_sse_receives_expansion(client):
    sse_resp = await client.get("/api/stream")
    assert sse_resp.status == 200
    assert "text/event-stream" in sse_resp.headers.get("Content-Type", "")

    lines_read = []
    try:
        for _ in range(4):
            line = await asyncio.wait_for(sse_resp.content.readline(), timeout=3)
            lines_read.append(line.decode())
    except asyncio.TimeoutError:
        pass

    initial = "".join(lines_read)
    assert "stats" in initial or "data" in initial

    import_data = {
        "nodes": [
            {"id": "test_a", "name": "Test A", "year": 2000},
            {"id": "test_b", "name": "Test B", "year": 2001},
        ],
        "links": [{"source": "test_a", "target": "test_b", "weight": 0.5}]
    }
    await client.post("/api/import", json=import_data)

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


async def test_graph_persists_restart(client):
    import_data = {
        "nodes": [
            {"id": "persist_a", "name": "Persist A", "year": 2000},
            {"id": "persist_b", "name": "Persist B", "year": 2001},
        ],
        "links": [{"source": "persist_a", "target": "persist_b", "weight": 0.7}]
    }
    resp = await client.post("/api/import", json=import_data)
    assert resp.status == 200

    db_path = client.app["db"].path

    db2 = GraphDB(db_path)
    stats = db2.get_stats()
    assert stats["nodes"] >= 2
    assert stats["edges"] >= 1
    db2.close()


async def test_cluster_naming(client):
    has_key = any(os.environ.get(k) for k in [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY", "XAI_API_KEY"
    ])
    if not has_key:
        pytest.skip("No LLM API key configured")

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
