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
        return {"nodes": len(self._nodes), "edges": len(self._edges)}


from generator import _parse_json_response, _parse_text_concepts, expand_once, expansion_loop, name_clusters, call_llm, _build_user_prompt


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

def test_build_prompt():
    prompt = _build_user_prompt("physics", ["Math", "Chemistry"], count=10)
    assert "10" in prompt
    assert "physics" in prompt.lower()
    assert "Math" in prompt


# --- make_id tests ---

def test_make_id():
    from generator import _make_id
    assert _make_id("Quantum Mechanics", set()) == "quantum_mechanics"
    assert _make_id("DNA", set()) == "dna"
    assert _make_id("Quantum Mechanics", {"quantum_mechanics"}) == "quantum_mechanics_2"
    result = _make_id("C++", set())
    assert isinstance(result, str) and len(result) > 0


# --- expand_once tests ---

async def test_expand_once_deduplicates():
    db = MockDB()
    db.add_node({"id": "existing", "name": "Existing", "year": 2000})

    mock_response = "Existing, 2000, 0.5\nNew One, 2001, 0.7"
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await expand_once("test", db, "openai")
    new_ids = {n["id"] for n in result["nodes"]}
    assert "new_one" in new_ids or any("new" in nid for nid in new_ids)


async def test_expand_once_validates():
    db = MockDB()
    mock_response = "just some junk\nValid, 2000, 0.5"
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await expand_once("test", db, "openai")
    names = [n["name"] for n in result["nodes"]]
    assert "Valid" in names


async def test_expand_once_clamps_weight():
    db = MockDB()
    mock_response = "A, 2000, 1.5\nB, 2000, -0.3"
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await expand_once("test", db, "openai")
    for edge in result.get("edges", []):
        assert 0.0 <= edge["weight"] <= 1.0


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
        return "\n".join(f"N{i}, 2000, 0.5" for i in range(3))

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
        return f"N{call_count}, 2000, 0.5"

    with patch("generator.call_llm", side_effect=flaky_llm):
        await expansion_loop("seed", db, "openai", callback=cb, max_rounds=2)
    assert "error" in events
    assert "expand" in events or "done" in events


# --- name_clusters test ---

async def test_name_clusters():
    mock_response = '{"names": ["Ancient Mathematics", "Modern Physics"]}'
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
