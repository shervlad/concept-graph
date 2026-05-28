# Chunk 7: Remove Domains from Database and API

## Goal

Strip the domains concept from the data layer. After this chunk, nodes are just `(id, name, year, desc)` with no domain categorization.

## Changes

### `db.py`

**Remove entirely:**
- `_domain_color()` function and its `@lru_cache` decorator
- `_ensure_domain()` method
- `self._known_domains` attribute (and its initialization in `__init__` and `_init_tables`)
- `import hashlib` and `from functools import lru_cache` (only used for domain colors)

**`_init_tables`:**
- Remove `domains TEXT DEFAULT '[]'` column from `nodes` table
- Remove `CREATE TABLE IF NOT EXISTS domains` statement
- Remove the loop that populates `_known_domains`

**`add_node`:**
- Remove all domain handling. Insert becomes:
  ```python
  cursor = self._conn.execute(
      "INSERT OR IGNORE INTO nodes (id, name, year, desc) VALUES (?, ?, ?, ?)",
      (node["id"], node["name"], node.get("year"), node.get("desc", ""))
  )
  ```

**`get_graph`:**
- Remove `domains` dict from return value
- Remove `domains` field from node dicts (drop `json.loads(r["domains"])`)
- Return: `{"nodes": [...], "links": [...], "total_nodes": N, "total_edges": N}`

**`get_stats`:**
- Remove domains count query
- Return: `{"nodes": N, "edges": N}`

**`import_json`:**
- Remove `explicit_domains` handling
- Remove domain processing from node loop
- Node row becomes: `(node["id"], node["name"], node.get("year"), node.get("desc", ""))`

### `server.py`

- No code changes needed (it just passes through `db.get_graph()` and `db.get_stats()` results)

### `seed_data.json`

- Remove `"domains"` array from every node object
- Keep: `id`, `name`, `year`, `desc`

### `test_db.py`

- **Delete** `test_domains_auto_created` and `test_domain_color_deterministic`
- **Update** `test_add_node`: remove domain field from test node, remove domain assertion
- **Update** `test_import_json` and `test_import_json_multi_domain`: remove domain assertions, merge into one test if they're now redundant
- **Update** all `get_graph` assertions: no `domains` key in response, no `domains` field in nodes
- **Update** `get_stats` assertions: no `domains` key

### `test_server.py`

- Remove `domains` assertions from graph and stats endpoint tests
- Remove domain count checks

### `test_integration.py`

- Remove `stats["domains"] >= 10` assertion
- Remove `len(graph["domains"]) >= 10` assertion
- Remove `assert "domains" in node` from node field checks

## Migration Note

The existing `knowledge.db` file has the old schema with domains. Delete it before running:
```bash
rm -f ~/projects/knowledge-tree/knowledge.db
```

## Verification

```bash
cd ~/projects/knowledge-tree
rm -f knowledge.db
pytest test_db.py test_server.py test_integration.py -v
```
