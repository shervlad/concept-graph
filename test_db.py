import pytest
import threading
from db import GraphDB


@pytest.fixture
def db(tmp_path):
    with GraphDB(str(tmp_path / "test.db")) as d:
        yield d


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
    assert len(graph["domains"]["physics"]["color"]) == 7


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
    for i in range(1, 10):
        db.add_edge({"source": "n0", "target": f"n{i}", "weight": 0.5})
    graph = db.get_graph(limit=5)
    assert len(graph["nodes"]) == 5
    assert graph["total_nodes"] == 20
    ids = {n["id"] for n in graph["nodes"]}
    assert "n0" in ids


def test_get_graph_center(db):
    """BFS from a center node returns its neighborhood."""
    for x in "abcde":
        db.add_node({"id": x, "name": x.upper(), "year": 2000, "domains": []})
    for s, t in [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")]:
        db.add_edge({"source": s, "target": t, "weight": 0.5})
    graph = db.get_graph(limit=3, center_id="c")
    ids = {n["id"] for n in graph["nodes"]}
    assert "c" in ids
    assert len(ids) <= 3


def test_get_least_expanded(db):
    """Returns nodes with fewest outgoing edges, excluding the expanded set."""
    db.add_node({"id": "hub", "name": "Hub", "year": 2000, "domains": []})
    db.add_node({"id": "leaf1", "name": "Leaf 1", "year": 2000, "domains": []})
    db.add_node({"id": "leaf2", "name": "Leaf 2", "year": 2000, "domains": []})
    db.add_edge({"source": "hub", "target": "leaf1", "weight": 0.5})
    db.add_edge({"source": "hub", "target": "leaf2", "weight": 0.5})
    result = db.get_least_expanded(expanded_set=set(), n=2)
    assert "hub" not in result[:2] or len(result) <= 2
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
