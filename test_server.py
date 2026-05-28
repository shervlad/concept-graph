import os
import pytest
import json
import asyncio
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

from server import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def client(aiohttp_client, app):
    return aiohttp_client(app)


async def test_index_served(client):
    resp = await (await client).get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "html" in text.lower()


async def test_graph_empty(client):
    resp = await (await client).get("/api/graph")
    assert resp.status == 200
    data = await resp.json()
    assert data["nodes"] == []
    assert data["links"] == []
    assert data["total_nodes"] == 0


async def test_graph_with_data(client):
    c = await client
    import_data = {
        "nodes": [
            {"id": "a", "name": "A", "year": 2000},
            {"id": "b", "name": "B", "year": 2001},
        ],
        "links": [{"source": "a", "target": "b", "weight": 0.8}],
    }
    await c.post("/api/import", json=import_data)
    resp = await c.get("/api/graph")
    data = await resp.json()
    assert len(data["nodes"]) == 2
    assert len(data["links"]) == 1


async def test_graph_limit(client):
    c = await client
    import_data = {
        "nodes": [
            {"id": f"n{i}", "name": f"N{i}", "year": 2000}
            for i in range(20)
        ],
        "links": [],
    }
    await c.post("/api/import", json=import_data)
    resp = await c.get("/api/graph?limit=5")
    data = await resp.json()
    assert len(data["nodes"]) <= 5
    assert data["total_nodes"] == 20


async def test_stats(client):
    c = await client
    import_data = {
        "nodes": [
            {"id": "a", "name": "A", "year": 2000},
            {"id": "b", "name": "B", "year": 2001},
        ],
        "links": [{"source": "a", "target": "b", "weight": 0.5}],
    }
    await c.post("/api/import", json=import_data)
    resp = await c.get("/api/stats")
    data = await resp.json()
    assert data["nodes"] == 2
    assert data["edges"] == 1


async def test_backends(client):
    c = await client
    resp = await c.get("/api/backends")
    data = await resp.json()
    assert set(data.keys()) == {"openai", "claude", "gemini", "deepseek", "grok"}
    for name, info in data.items():
        assert "model" in info
        assert "configured" in info
        assert isinstance(info["configured"], bool)


async def test_import(client):
    c = await client
    import_data = {
        "nodes": [{"id": "x", "name": "X", "year": 2000}],
        "links": [],
    }
    resp = await c.post("/api/import", json=import_data)
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "imported"
    assert body["nodes"] == 1


async def test_expand_no_seed(client):
    c = await client
    resp = await c.post("/api/expand", json={"seed": "", "backend": "openai"})
    assert resp.status == 400


async def test_expand_bad_backend(client):
    c = await client
    resp = await c.post("/api/expand", json={"seed": "test", "backend": "nonexistent"})
    assert resp.status == 400


async def test_expand_no_api_key(client):
    c = await client
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("XAI_API_KEY", None)
        resp = await c.post("/api/expand", json={"seed": "test", "backend": "grok"})
    assert resp.status == 400
    data = await resp.json()
    assert "XAI_API_KEY" in data["error"]


async def test_stop_when_not_running(client):
    c = await client
    resp = await c.post("/api/stop")
    data = await resp.json()
    assert data["status"] == "not running"


async def test_sse_connects(client):
    c = await client
    resp = await c.get("/api/stream")
    assert resp.status == 200
    assert "text/event-stream" in resp.headers.get("Content-Type", "")
    chunk = await resp.content.readline()
    assert b"event:" in chunk or b"data:" in chunk
    resp.close()


async def test_sse_receives_import_broadcast(client):
    c = await client
    sse_resp = await c.get("/api/stream")
    for _ in range(3):
        await asyncio.wait_for(sse_resp.content.readline(), timeout=2)

    import_data = {
        "nodes": [{"id": "x", "name": "X", "year": 2000}],
        "links": [],
    }
    await c.post("/api/import", json=import_data)

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


async def test_cluster_names(client):
    c = await client
    body = {
        "clusters": [
            {"names": ["Calculus", "Algebra"]},
            {"names": ["Quantum Mechanics"]},
        ],
        "backend": "openai",
    }
    resp = await c.post("/api/cluster-names", json=body)
    # Will fail if no API key, but should return 200 or 500 (not crash)
    assert resp.status in (200, 500)
    data = await resp.json()
    if resp.status == 200:
        assert len(data["names"]) == 2
    else:
        assert "error" in data


async def test_static_serves_js(client):
    c = await client
    resp = await c.get("/force-worker.js")
    assert resp.status == 200
    text = await resp.text()
    assert len(text) > 0


async def test_static_rejects_path_traversal(client):
    c = await client
    resp = await c.get("/../../etc/passwd")
    assert resp.status == 404


async def test_static_rejects_disallowed_extension(client):
    c = await client
    resp = await c.get("/db.py")
    assert resp.status == 404


async def test_graph_center_param(client):
    c = await client
    import_data = {
        "nodes": [
            {"id": "a", "name": "A", "year": 2000},
            {"id": "b", "name": "B", "year": 2001},
        ],
        "links": [{"source": "a", "target": "b", "weight": 0.5}],
    }
    await c.post("/api/import", json=import_data)
    resp = await c.get("/api/graph?center=a")
    assert resp.status == 200
    data = await resp.json()
    assert any(n["id"] == "a" for n in data["nodes"])


async def test_graph_invalid_limit(client):
    c = await client
    resp = await c.get("/api/graph?limit=abc")
    assert resp.status == 400


async def test_cors_headers(client):
    c = await client
    resp = await c.get("/api/stats")
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
