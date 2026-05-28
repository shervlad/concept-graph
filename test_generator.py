import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock


class MockDB:
    def __init__(self):
        self._nodes = {}
        self._edges = []

    def add_node(self, node):
        if node["id"] in self._nodes:
            return False
        self._nodes[node["id"]] = node
        return True

    def add_edge(self, edge):
        if edge["source"] not in self._nodes or edge["target"] not in self._nodes:
            return False
        self._edges.append(edge)
        return True

    def get_node_ids(self):
        return set(self._nodes.keys())

    def get_node_names_by_ids(self, ids):
        return {i: self._nodes[i]["name"] for i in ids if i in self._nodes}

    def get_least_expanded(self, expanded_set, n=5):
        return [k for k in self._nodes if k not in expanded_set][:n]

    def get_stats(self):
        return {"nodes": len(self._nodes), "edges": len(self._edges), "domains": 0}


from generator import _parse_json_response, expand_once, expansion_loop, name_clusters, call_llm, _build_user_prompt


# --- Parsing tests ---

def test_parse_json_clean():
    result = _parse_json_response('{"nodes": [], "edges": []}')
    assert result == {"nodes": [], "edges": []}


def test_parse_json_with_fences():
    text = '```json\n{"nodes": [{"id": "x"}], "edges": []}\n```'
    result = _parse_json_response(text)
    assert result["nodes"][0]["id"] == "x"


def test_parse_json_with_preamble():
    text = 'Here is the result:\n{"nodes": [], "edges": []}'
    result = _parse_json_response(text)
    assert "nodes" in result


# --- Prompt construction tests ---

def test_build_seed_prompt():
    prompt = _build_user_prompt("mathematics", {}, is_seed=True)
    assert "mathematics" in prompt.lower()
    assert "EXISTING CONCEPTS" not in prompt


def test_build_expansion_prompt():
    existing = {"calc": "Calculus", "alg": "Algebra"}
    prompt = _build_user_prompt("geometry", existing, is_seed=False)
    assert "geometry" in prompt.lower()
    assert "calc" in prompt
    assert "Calculus" in prompt
    assert "EXISTING CONCEPTS" in prompt


# --- expand_once tests ---

async def test_expand_once_deduplicates():
    db = MockDB()
    db.add_node({"id": "existing", "name": "Existing", "year": 2000, "domains": []})

    mock_response = {
        "nodes": [
            {"id": "existing", "name": "Existing Dupe", "year": 2000, "domains": ["math"]},
            {"id": "new_one", "name": "New One", "year": 2001, "domains": ["math"]},
        ],
        "edges": []
    }
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await expand_once("test", db, "openai")
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["id"] == "new_one"


async def test_expand_once_validates():
    db = MockDB()
    mock_response = {
        "nodes": [
            {"name": "No ID", "year": 2000, "domains": ["math"]},
            {"id": "valid", "name": "Valid", "year": 2000, "domains": ["math"]},
        ],
        "edges": []
    }
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await expand_once("test", db, "openai")
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["id"] == "valid"


async def test_expand_once_clamps_weight():
    db = MockDB()
    mock_response = {
        "nodes": [
            {"id": "a", "name": "A", "year": 2000, "domains": []},
            {"id": "b", "name": "B", "year": 2000, "domains": []},
        ],
        "edges": [
            {"source": "a", "target": "b", "weight": 1.5},
            {"source": "b", "target": "a", "weight": -0.3},
        ]
    }
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await expand_once("test", db, "openai")
    weights = [e["weight"] for e in result["edges"]]
    assert all(0.0 <= w <= 1.0 for w in weights)


# --- expansion_loop tests ---

async def test_expansion_loop_stops_on_cancel():
    db = MockDB()
    events = []

    async def cb(event, data):
        events.append(event)

    call_count = 0
    async def cancelling_llm(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()
        return {
            "nodes": [{"id": f"n{i}", "name": f"N{i}", "year": 2000, "domains": []} for i in range(3)],
            "edges": []
        }

    with patch("generator.call_llm", side_effect=cancelling_llm):
        await expansion_loop("seed", db, "openai", callback=cb, max_rounds=5)
    assert "stopped" in events


async def test_expansion_loop_continues_on_error():
    db = MockDB()
    events = []
    call_count = 0

    async def cb(event, data):
        events.append(event)

    async def flaky_llm(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("API error")
        return {
            "nodes": [{"id": f"n{call_count}", "name": f"N{call_count}", "year": 2000, "domains": []}],
            "edges": []
        }

    with patch("generator.call_llm", side_effect=flaky_llm):
        await expansion_loop("seed", db, "openai", callback=cb, max_rounds=2)
    assert "error" in events
    assert "expand" in events or "done" in events


# --- name_clusters test ---

async def test_name_clusters():
    mock_response = {"names": ["Ancient Mathematics", "Modern Physics"]}
    clusters = [
        {"names": ["Calculus", "Algebra"]},
        {"names": ["Quantum Mechanics", "Relativity"]},
    ]
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        names = await name_clusters(clusters, "openai")
    assert len(names) == 2
    assert names[0] == "Ancient Mathematics"


# --- Backend validation test ---

async def test_backend_missing_key():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            await call_llm("test prompt", "openai")
