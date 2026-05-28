# Chunk 2: LLM Generator

## Project Context

We're building "Knowledge Tree" — a web app that visualizes human knowledge as a force-directed graph. An LLM expands the graph from a seed word, the browser renders it in real time with WebGL.

This chunk implements the **multi-backend LLM generator** that expands the knowledge graph. It calls LLM APIs to generate new concepts and connections, then writes them to the database.

**Working directory:** `~/projects/knowledge-tree/`

## Files to Create

- `generator.py` — LLM backend adapters, prompt construction, expansion logic, cluster naming
- `test_generator.py` — 12 pytest tests with mocked LLM calls (no real API calls)

## Dependencies

```bash
pip install openai anthropic google-generativeai pytest pytest-asyncio
```

Also depends on `db.py` from Chunk 1. For testing, you'll need a minimal mock or the real `db.py` if available.

## Dependency Interface: `GraphDB` (from Chunk 1)

If `db.py` isn't implemented yet, create a mock that satisfies this interface:

```python
class GraphDB:
    def add_node(self, node: dict) -> bool:
        """Insert node {id, name, year, domains, desc}. Returns False if id already exists."""
    def add_edge(self, edge: dict) -> bool:
        """Insert edge {source, target, weight}. Returns False if exists or nodes missing."""
    def get_node_ids(self) -> set[str]:
        """Return set of all node IDs."""
    def get_node_names_by_ids(self, ids: list[str]) -> dict[str, str]:
        """Return {id: name} for requested IDs."""
    def get_least_expanded(self, expanded_set: set[str], n: int = 5) -> list[str]:
        """Return node IDs with fewest outgoing edges, excluding expanded_set."""
    def get_stats(self) -> dict:
        """Return {"nodes": int, "edges": int, "domains": int}."""
```

## Backend Configuration

```python
BACKENDS = {
    "openai": {
        "sdk": "openai",
        "model": "gpt-4o",
        "env": "OPENAI_API_KEY",
    },
    "claude": {
        "sdk": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "env": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "sdk": "gemini",
        "model": "gemini-2.0-flash",
        "env": "GOOGLE_API_KEY",
    },
    "deepseek": {
        "sdk": "openai",            # uses OpenAI-compatible endpoint
        "model": "deepseek-chat",
        "env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
    },
    "grok": {
        "sdk": "openai",            # uses OpenAI-compatible endpoint
        "model": "grok-3",
        "env": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
    },
}
```

DeepSeek and Grok use the `openai` SDK with a custom `base_url`. No extra SDKs needed.

## Full Interface Contract

```python
async def call_llm(prompt: str, backend: str = "openai") -> dict:
    """
    Send a prompt to the specified LLM backend and return the parsed JSON response.

    Raises ValueError if the backend's API key env var is not set.
    Raises on network/API errors (caller should catch).

    Implementation per SDK:
      openai/deepseek/grok: AsyncOpenAI(api_key=..., base_url=...).chat.completions.create(
          model=..., messages=[system, user], response_format={"type": "json_object"},
          temperature=0.7, max_tokens=4096)
      anthropic: AsyncAnthropic(api_key=...).messages.create(
          model=..., system=..., messages=[user], temperature=0.7, max_tokens=4096)
      gemini: genai.GenerativeModel(model).generate_content(
          prompt, generation_config=GenerationConfig(response_mime_type="application/json", ...))
          — run in asyncio.to_thread since google-genai is sync
    """

async def expand_once(focal: str, db: GraphDB, backend: str = "openai") -> dict:
    """
    Single expansion round. Asks the LLM to generate ~15 concepts related to `focal`.

    1. Get existing node IDs and names from db
    2. Build prompt (seed mode if db is empty, expansion mode otherwise)
    3. Call LLM
    4. Parse response, validate nodes, deduplicate against existing IDs
    5. Write valid new nodes and edges to db
    6. Return {"nodes": [<new nodes>], "edges": [<new edges>]}
    """

async def expansion_loop(
    seed: str,
    db: GraphDB,
    backend: str = "openai",
    callback = None,     # async def callback(event: str, data: dict)
    max_rounds: int = 0  # 0 = unlimited
):
    """
    Continuous expansion loop.

    Round 0: expand from seed word
    Round 1+: pick least-expanded concept from db, expand around it

    After each round, call:
        await callback("expand", {
            "round": int,
            "focal": str,
            "new_nodes": [...],
            "new_edges": [...],
            "stats": {"nodes": N, "edges": N, "domains": N}
        })

    On error in a round:
        await callback("error", {"message": str, "focal": str})
        — then continue to next round (don't crash the loop)

    On asyncio.CancelledError:
        await callback("stopped", {"stats": {...}})
        — then return

    When done (no more candidates or max_rounds reached):
        await callback("done", {"stats": {...}})
    """

async def name_clusters(clusters: list[dict], backend: str = "openai") -> list[str]:
    """
    Ask an LLM to name spatial clusters of concepts.

    clusters: [{"names": ["Calculus", "Algebra", "Geometry", ...]}, ...]
    Returns: ["Classical Mathematics", "Modern Physics", ...]

    Each cluster's names list may have up to 15 concept names.
    The LLM should return a short 2-4 word label for each.
    """
```

## Prompt Design

### System Prompt (used for all expansion calls)

```
You are a knowledge graph builder creating a comprehensive tree of human knowledge.
You output ONLY valid JSON — no markdown, no commentary, no code fences.
Every concept must have a historically accurate year of first appearance.
Connections must have meaningful weights reflecting intellectual dependency strength.
```

### Seed Mode User Prompt (when db is empty)

```
Create the foundational concept "{seed}" and 15 closely related concepts that form
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
{"nodes": [...], "edges": [...]}
```

### Expansion Mode User Prompt (when db has nodes)

```
Expand the knowledge graph around the concept "{focal}".

EXISTING CONCEPTS (do NOT duplicate — but DO connect to them):
  - algebra: Algebra
  - calculus: Calculus
  ... (up to 200 entries, truncated if more)

Generate 15 NEW concepts closely related to "{focal}" that are missing from the graph.
Include concepts that:
- Are prerequisites or foundations for "{focal}"
- Were directly influenced by or built upon "{focal}"
- Are sibling concepts in the same intellectual tradition
- Bridge "{focal}" to other domains

For each new concept:
- id: unique snake_case, NOT in the existing list above
- name: human-readable (1-5 words)
- year: integer year first formulated (negative for BC)
- domains: array of 1-3 category strings (lowercase_snake_case)
- desc: one sentence description

Then provide edges connecting new concepts to existing ones AND to each other:
- source, target: concept ids (can reference existing or new)
- weight: 0.0-1.0

Respond with ONLY this JSON:
{"nodes": [...], "edges": [...]}
```

### Cluster Naming Prompt

```
Given these clusters of knowledge concepts, provide a short descriptive name (2-4 words) for each cluster.

Clusters:
1. [Calculus, Algebra, Geometry, Number Theory]
2. [Quantum Mechanics, General Relativity, Electromagnetism]

Respond with ONLY this JSON:
{"names": ["Cluster 1 Name", "Cluster 2 Name"]}
```

## JSON Response Parsing

LLMs sometimes wrap JSON in markdown fences or add preamble text. The parser must handle:

1. Clean JSON: `{"nodes": [...]}`
2. Markdown fences: `` ```json\n{"nodes": [...]}\n``` ``
3. Preamble: `Here is the JSON:\n{"nodes": [...]}`
4. Trailing text: `{"nodes": [...]} Hope this helps!`

```python
def _parse_json_response(text: str) -> dict:
    """
    Extract and parse JSON from an LLM response that may contain
    markdown fences, preamble, or trailing text.
    Raises json.JSONDecodeError if no valid JSON found.
    """
```

## Validation Rules

When processing LLM output in `expand_once`:

- **Nodes**: Skip any node missing `id` or `name`. Skip if `id` already exists in db.
- **Domains**: If `domains` is a string, convert to `[domains]`. If missing, default to `["unknown"]`.
- **Year**: If missing or not an integer, default to `None`.
- **Edges**: Skip any edge missing `source` or `target`. Skip if source or target doesn't exist (neither in db nor in the current batch of new nodes). Clamp weight to [0.0, 1.0].

## Tests (`test_generator.py`)

Use `pytest-asyncio` for async tests. Mock all LLM calls — no real API requests.

```python
import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

# If db.py is available, use it. Otherwise, create a simple mock:
class MockDB:
    def __init__(self):
        self._nodes = {}  # id -> node dict
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
    """Valid JSON string parses correctly."""
    result = _parse_json_response('{"nodes": [], "edges": []}')
    assert result == {"nodes": [], "edges": []}

def test_parse_json_with_fences():
    """JSON wrapped in markdown code fences parses correctly."""
    text = '```json\n{"nodes": [{"id": "x"}], "edges": []}\n```'
    result = _parse_json_response(text)
    assert result["nodes"][0]["id"] == "x"

def test_parse_json_with_preamble():
    """JSON preceded by non-JSON text parses correctly."""
    text = 'Here is the result:\n{"nodes": [], "edges": []}'
    result = _parse_json_response(text)
    assert "nodes" in result

# --- Prompt construction tests ---

def test_build_seed_prompt():
    """Empty graph produces a seed-mode prompt containing the seed word."""
    prompt = _build_user_prompt("mathematics", {}, is_seed=True)
    assert "mathematics" in prompt.lower()
    assert "EXISTING CONCEPTS" not in prompt

def test_build_expansion_prompt():
    """Graph with nodes produces an expansion prompt listing existing IDs."""
    existing = {"calc": "Calculus", "alg": "Algebra"}
    prompt = _build_user_prompt("geometry", existing, is_seed=False)
    assert "geometry" in prompt.lower()
    assert "calc" in prompt
    assert "Calculus" in prompt
    assert "EXISTING CONCEPTS" in prompt

# --- expand_once tests ---

@pytest.mark.asyncio
async def test_expand_once_deduplicates():
    """Nodes already in the db are skipped."""
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

@pytest.mark.asyncio
async def test_expand_once_validates():
    """Nodes missing required fields are skipped."""
    db = MockDB()
    mock_response = {
        "nodes": [
            {"name": "No ID", "year": 2000, "domains": ["math"]},  # missing id
            {"id": "valid", "name": "Valid", "year": 2000, "domains": ["math"]},
        ],
        "edges": []
    }
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await expand_once("test", db, "openai")
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["id"] == "valid"

@pytest.mark.asyncio
async def test_expand_once_clamps_weight():
    """Edge weights are clamped to [0.0, 1.0]."""
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

@pytest.mark.asyncio
async def test_expansion_loop_stops_on_cancel():
    """Cancelling the loop triggers a 'stopped' callback."""
    db = MockDB()
    events = []
    async def cb(event, data):
        events.append(event)
        if event == "expand":
            raise asyncio.CancelledError()  # simulate cancel after first round

    mock_response = {
        "nodes": [{"id": f"n{i}", "name": f"N{i}", "year": 2000, "domains": []} for i in range(3)],
        "edges": []
    }
    with patch("generator.call_llm", new_callable=AsyncMock, return_value=mock_response):
        await expansion_loop("seed", db, "openai", callback=cb, max_rounds=5)
    assert "stopped" in events

@pytest.mark.asyncio
async def test_expansion_loop_continues_on_error():
    """An error in one round doesn't stop the loop."""
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

@pytest.mark.asyncio
async def test_name_clusters():
    """Returns a list of cluster names matching the number of input clusters."""
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

@pytest.mark.asyncio
async def test_backend_missing_key():
    """Calling with an unconfigured backend raises ValueError."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            await call_llm("test prompt", "openai")
```

## Implementation Notes

- Make `_parse_json_response` and `_build_user_prompt` module-level functions (not methods) so tests can import them directly.
- Use `logging` module for all status messages (not print).
- The `expansion_loop` must handle `asyncio.CancelledError` gracefully — call the callback then return, don't re-raise.
- For the gemini backend, `google.generativeai` is synchronous — wrap in `asyncio.to_thread()`.
- The prompt should list up to ~200 existing concept IDs. If the graph has more, truncate (pick the most-connected ones to give the LLM better context).

## Acceptance Criteria

```bash
cd ~/projects/knowledge-tree
pytest test_generator.py -v
# All 12 tests must pass
# No real API calls are made (everything mocked)
```
