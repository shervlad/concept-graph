# Chunk 1: Database Layer

## Project Context

We're building "Knowledge Tree" — a web app that visualizes human knowledge as a force-directed graph. An LLM expands the graph from a seed word, the browser renders it in real time with WebGL.

This chunk implements the **SQLite persistence layer**. Every other backend component depends on it.

**Working directory:** `~/projects/knowledge-tree/`

## Files to Create

- `db.py` — the `GraphDB` class
- `test_db.py` — 14 pytest tests

## Schema

```sql
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,              -- snake_case unique key, e.g. "general_relativity"
    name TEXT NOT NULL,               -- human-readable, e.g. "General Relativity"
    year INTEGER,                     -- year of first appearance (negative for BC, e.g. -300)
    domains TEXT DEFAULT '[]',        -- JSON array of domain keys, e.g. '["physics","mathematics"]'
    desc TEXT DEFAULT '',             -- one-sentence description
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE edges (
    source TEXT NOT NULL,             -- FK → nodes.id
    target TEXT NOT NULL,             -- FK → nodes.id
    weight REAL DEFAULT 0.5,          -- 0.0–1.0, strength of intellectual dependency
    PRIMARY KEY (source, target),
    FOREIGN KEY (source) REFERENCES nodes(id),
    FOREIGN KEY (target) REFERENCES nodes(id)
);

CREATE TABLE domains (
    key TEXT PRIMARY KEY,             -- e.g. "physics"
    label TEXT NOT NULL,              -- e.g. "Physics"
    color TEXT NOT NULL               -- hex color, e.g. "#ff8a65"
);

-- Indexes for subgraph queries
CREATE INDEX idx_edges_source ON edges(source);
CREATE INDEX idx_edges_target ON edges(target);
```

**Important:** Domains are NOT hardcoded. They are auto-created whenever a node references a domain that doesn't exist yet. The color is deterministically generated from the domain key via MD5 hash → HSL.

## Full Interface Contract

```python
import sqlite3
import json
import hashlib
import threading

class GraphDB:
    def __init__(self, path: str = "knowledge.db"):
        """
        Open or create the SQLite database at `path`.
        Enable WAL mode for concurrent reads.
        Create tables if they don't exist.
        Must be safe to call from multiple threads (use a threading.Lock for writes).
        """

    def add_node(self, node: dict) -> bool:
        """
        Insert a node. Returns True if inserted, False if id already exists.
        Never overwrites existing nodes.

        node dict keys:
            id: str (required)
            name: str (required)
            year: int or None
            domains: list[str] (default []) — e.g. ["physics", "mathematics"]
                     May also be a single string "physics" → treat as ["physics"]
            desc: str (default "")

        Side effect: for each domain in the list, ensure a row exists in the
        `domains` table. If the domain is new, auto-generate:
            label = key.replace("_", " ").title()
            color = deterministic hex color from MD5 hash of key
        """

    def add_edge(self, edge: dict) -> bool:
        """
        Insert an edge. Returns True if inserted, False if (source, target) already exists.
        Never overwrites existing edges.

        edge dict keys:
            source: str (required) — must be an existing node id
            target: str (required) — must be an existing node id
            weight: float (default 0.5) — clamped to [0.0, 1.0]

        If source or target doesn't exist in nodes table, fail gracefully (return False).
        """

    def get_graph(self, limit: int = 500, center_id: str = None) -> dict:
        """
        Return a subgraph.

        If center_id is None:
            Return the top `limit` nodes sorted by connection count (descending).

        If center_id is provided:
            BFS outward from center_id, collecting up to `limit` nodes.

        Return format:
        {
            "domains": {"physics": {"color": "#ff8a65", "label": "Physics"}, ...},
            "nodes": [{"id": "...", "name": "...", "year": N, "domains": [...], "desc": "..."}, ...],
            "links": [{"source": "id1", "target": "id2", "weight": 0.8}, ...],
            "total_nodes": <int>,    # total nodes in entire DB, not just this subgraph
            "total_edges": <int>     # total edges in entire DB
        }

        Only include edges where BOTH source and target are in the returned node set.
        Include ALL domains from the domains table, not just those referenced by returned nodes.
        """

    def get_node_ids(self) -> set[str]:
        """Return the set of all node IDs in the database."""

    def get_node_names_by_ids(self, ids: list[str]) -> dict[str, str]:
        """
        Given a list of node IDs, return {id: name} for those that exist.
        IDs not found are silently omitted.
        """

    def get_least_expanded(self, expanded_set: set[str], n: int = 5) -> list[str]:
        """
        Return up to `n` node IDs that have the fewest outgoing edges,
        excluding any ID in `expanded_set`.
        Sorted ascending by outgoing edge count.
        Used by the generator to pick which concept to expand next.
        """

    def get_stats(self) -> dict:
        """Return {"nodes": int, "edges": int, "domains": int}."""

    def import_json(self, data: dict) -> None:
        """
        Bulk import from a JSON structure. Accepts TWO formats:

        Format A (with explicit domains):
        {
            "domains": {"physics": {"color": "#ff8a65", "label": "Physics"}, ...},
            "nodes": [{"id": ..., "name": ..., "year": ..., "domains": [...], "desc": ...}, ...],
            "links": [{"source": ..., "target": ..., "weight": ...}, ...]
        }

        Format B (legacy, single domain per node):
        {
            "nodes": [{"id": ..., "name": ..., "year": ..., "domain": "physics", "desc": ...}, ...],
            "links": [{"source": ..., "target": ..., "weight": ...}, ...]
        }

        For Format B, convert node.domain → node.domains = [node.domain].
        For both formats, domains are auto-created from node data if not in the domains dict.
        If explicit domains are provided in the "domains" key, use those colors/labels
        instead of auto-generating.
        """

    def close(self):
        """Close the database connection."""
```

## Domain Color Generation

When a new domain is encountered that doesn't exist in the `domains` table, auto-generate a color:

```python
def _domain_color(key: str) -> str:
    """
    Deterministic hex color from domain key.
    Uses MD5 hash → hue (0-360), with fixed saturation=65%, lightness=60%.
    Convert HSL to hex RGB.
    """
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % 360
    s, l = 0.65, 0.60
    # ... HSL to RGB conversion ...
    return "#rrggbb"
```

This ensures the same domain key always gets the same color, across sessions and machines.

## Tests (`test_db.py`)

Use `pytest` with `tmp_path` fixture for isolated databases. Each test gets its own DB file.

```python
import pytest
from db import GraphDB

@pytest.fixture
def db(tmp_path):
    d = GraphDB(str(tmp_path / "test.db"))
    yield d
    d.close()

def test_create_empty_db(db):
    """Fresh database has zero nodes, edges, domains."""
    stats = db.get_stats()
    assert stats == {"nodes": 0, "edges": 0, "domains": 0}

def test_add_node(db):
    """Add a single node, verify it appears in get_graph()."""
    result = db.add_node({"id": "calculus", "name": "Calculus", "year": 1687,
                          "domains": ["mathematics"], "desc": "Mathematics of change"})
    assert result is True
    graph = db.get_graph()
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["id"] == "calculus"
    assert graph["nodes"][0]["domains"] == ["mathematics"]

def test_add_duplicate_node(db):
    """Adding a node with an existing ID returns False and does NOT overwrite."""
    db.add_node({"id": "x", "name": "Original", "year": 2000, "domains": []})
    result = db.add_node({"id": "x", "name": "Overwrite", "year": 1999, "domains": []})
    assert result is False
    graph = db.get_graph()
    assert graph["nodes"][0]["name"] == "Original"

def test_add_edge(db):
    """Add two nodes and an edge, verify edge in get_graph()."""
    db.add_node({"id": "a", "name": "A", "year": 2000, "domains": []})
    db.add_node({"id": "b", "name": "B", "year": 2001, "domains": []})
    result = db.add_edge({"source": "a", "target": "b", "weight": 0.8})
    assert result is True
    graph = db.get_graph()
    assert len(graph["links"]) == 1
    assert graph["links"][0]["weight"] == 0.8

def test_add_edge_missing_node(db):
    """Edge referencing nonexistent node returns False, no crash."""
    db.add_node({"id": "a", "name": "A", "year": 2000, "domains": []})
    result = db.add_edge({"source": "a", "target": "nonexistent", "weight": 0.5})
    assert result is False

def test_domains_auto_created(db):
    """Adding a node with domains=["physics"] auto-creates the domain entry."""
    db.add_node({"id": "x", "name": "X", "year": 2000, "domains": ["physics"]})
    graph = db.get_graph()
    assert "physics" in graph["domains"]
    assert graph["domains"]["physics"]["label"] == "Physics"
    assert graph["domains"]["physics"]["color"].startswith("#")
    assert len(graph["domains"]["physics"]["color"]) == 7  # #rrggbb

def test_domain_color_deterministic(db, tmp_path):
    """Same domain key produces same color in different database instances."""
    db.add_node({"id": "x", "name": "X", "year": 2000, "domains": ["physics"]})
    color1 = db.get_graph()["domains"]["physics"]["color"]
    db2 = GraphDB(str(tmp_path / "test2.db"))
    db2.add_node({"id": "y", "name": "Y", "year": 2000, "domains": ["physics"]})
    color2 = db2.get_graph()["domains"]["physics"]["color"]
    db2.close()
    assert color1 == color2

def test_get_graph_limit(db):
    """With 20 nodes, get_graph(limit=5) returns exactly 5, prioritized by connectivity."""
    for i in range(20):
        db.add_node({"id": f"n{i}", "name": f"Node {i}", "year": 2000, "domains": []})
    # Create edges to make n0 the most connected
    for i in range(1, 10):
        db.add_edge({"source": "n0", "target": f"n{i}", "weight": 0.5})
    graph = db.get_graph(limit=5)
    assert len(graph["nodes"]) == 5
    assert graph["total_nodes"] == 20
    # n0 should be included (most connected)
    ids = {n["id"] for n in graph["nodes"]}
    assert "n0" in ids

def test_get_graph_center(db):
    """BFS from a center node returns its neighborhood."""
    # Chain: a -> b -> c -> d -> e
    for x in "abcde":
        db.add_node({"id": x, "name": x.upper(), "year": 2000, "domains": []})
    for s, t in [("a","b"), ("b","c"), ("c","d"), ("d","e")]:
        db.add_edge({"source": s, "target": t, "weight": 0.5})
    graph = db.get_graph(limit=3, center_id="c")
    ids = {n["id"] for n in graph["nodes"]}
    assert "c" in ids
    # Should include neighbors of c (b and d), maybe not a or e depending on limit
    assert len(ids) <= 3

def test_get_least_expanded(db):
    """Returns nodes with fewest outgoing edges, excluding the expanded set."""
    db.add_node({"id": "hub", "name": "Hub", "year": 2000, "domains": []})
    db.add_node({"id": "leaf1", "name": "Leaf 1", "year": 2000, "domains": []})
    db.add_node({"id": "leaf2", "name": "Leaf 2", "year": 2000, "domains": []})
    db.add_edge({"source": "hub", "target": "leaf1", "weight": 0.5})
    db.add_edge({"source": "hub", "target": "leaf2", "weight": 0.5})
    result = db.get_least_expanded(expanded_set=set(), n=2)
    # leaf1 and leaf2 have 0 outgoing edges, hub has 2
    assert "hub" not in result[:2] or len(result) <= 2
    # Excluding leaf1
    result2 = db.get_least_expanded(expanded_set={"leaf1"}, n=2)
    assert "leaf1" not in result2

def test_import_json(db):
    """Import a full JSON structure with domains, nodes, and links."""
    data = {
        "domains": {"math": {"color": "#4fc3f7", "label": "Mathematics"}},
        "nodes": [
            {"id": "calc", "name": "Calculus", "year": 1687, "domains": ["math"], "desc": "Change"},
            {"id": "alg", "name": "Algebra", "year": 820, "domains": ["math"], "desc": "Equations"},
        ],
        "links": [
            {"source": "alg", "target": "calc", "weight": 1.0},
        ]
    }
    db.import_json(data)
    stats = db.get_stats()
    assert stats["nodes"] == 2
    assert stats["edges"] == 1
    assert stats["domains"] >= 1
    # Explicit color should be used, not auto-generated
    graph = db.get_graph()
    assert graph["domains"]["math"]["color"] == "#4fc3f7"

def test_import_json_multi_domain(db):
    """Node with domains=["physics","mathematics"] creates both domain entries."""
    data = {
        "nodes": [{"id": "x", "name": "X", "year": 2000, "domains": ["physics", "mathematics"]}],
        "links": []
    }
    db.import_json(data)
    graph = db.get_graph()
    assert "physics" in graph["domains"]
    assert "mathematics" in graph["domains"]
    assert graph["nodes"][0]["domains"] == ["physics", "mathematics"]

def test_concurrent_reads(db):
    """Multiple threads can read simultaneously without errors (WAL mode)."""
    import threading
    db.add_node({"id": "x", "name": "X", "year": 2000, "domains": []})
    errors = []
    def reader():
        try:
            for _ in range(50):
                db.get_graph()
                db.get_stats()
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=reader) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(errors) == 0

def test_node_names_by_ids(db):
    """Returns {id: name} for requested IDs, omitting missing ones."""
    db.add_node({"id": "a", "name": "Alpha", "year": 2000, "domains": []})
    db.add_node({"id": "b", "name": "Beta", "year": 2001, "domains": []})
    result = db.get_node_names_by_ids(["a", "b", "nonexistent"])
    assert result == {"a": "Alpha", "b": "Beta"}
```

## Implementation Notes

- Use `sqlite3.Row` as `row_factory` so you can access columns by name.
- Use `INSERT OR IGNORE` for add_node and add_edge to handle duplicates cleanly.
- For `add_edge`, check that both source and target exist in nodes table before inserting.
- The `get_graph` connectivity sort can use a LEFT JOIN to count edges per node.
- For BFS in `get_graph(center_id=...)`, use a Python-side BFS loop, not a recursive SQL CTE (simpler and handles the limit more naturally).
- The `import_json` method should handle both Format A (explicit domains dict) and Format B (legacy single `domain` field per node).

## Acceptance Criteria

```bash
cd ~/projects/knowledge-tree
pytest test_db.py -v
# All 14 tests must pass
```

No external dependencies needed — only Python stdlib (sqlite3, json, hashlib, threading).
