# Chunk 10: Update All Tests for Domain-Free BFS Expansion

## Goal

Update all test files to match the refactored system: no domains, BFS expansion, `{"concepts": [...]}` LLM format, server-generated IDs.

**Prerequisite:** Chunks 7–9

## Changes

### `test_db.py`

- **Delete** `test_domains_auto_created`, `test_domain_color_deterministic`
- **`test_add_node`**: Remove `domains` from node dict and assertions
- **`test_import_json`**: Remove domain assertions, update node dicts to not have `domains`
- **Delete** `test_import_json_multi_domain` (no longer relevant)
- **`test_get_graph_*`**: Remove `domains` from response assertions, remove `domains` from node field checks
- **`test_get_stats`** (if it exists as a separate test): Assert only `nodes` and `edges` keys

### `test_generator.py`

- **Replace `test_build_seed_prompt` and `test_build_expansion_prompt`** with `test_build_prompt`:
  - Call `_build_user_prompt("physics", ["Math", "Chemistry"], count=10)`
  - Assert "10" in prompt, "physics" in prompt, "Math" in prompt

- **Add `test_make_id`**:
  ```python
  def test_make_id():
      assert _make_id("Quantum Mechanics", set()) == "quantum_mechanics"
      assert _make_id("DNA", set()) == "dna"
      assert _make_id("Quantum Mechanics", {"quantum_mechanics"}) == "quantum_mechanics_2"
      assert _make_id("C++", set())  # handles special chars
  ```

- **`test_expand_once_deduplicates`**: MockDB pre-populated with a node. LLM returns `{"concepts": [{"name": "Existing Thing", "year": 2000, "weight": 0.5}]}`. Verify node is added with a unique suffixed ID (since name-to-ID might collide) or added normally if no collision.

- **`test_expand_once_validates`**: LLM returns concepts with missing name → skipped

- **`test_expand_once_clamps_weight`**: LLM returns weight > 1.0 → clamped to 1.0

- **`test_expansion_loop_stops_on_cancel`**: Mock `call_llm` raises `CancelledError` on second call. Verify callback receives "stopped" event.

- **`test_expansion_loop_continues_on_error`**: First `call_llm` raises generic error, second succeeds. Verify BFS continues — callback gets "error" then "expand" events.

- **Keep unchanged**: `test_parse_json_*`, `test_name_clusters`, `test_backend_missing_key`

### `test_server.py`

- Remove all `domains` assertions from graph/stats endpoint tests
- Remove `domains` from test data node dicts where present
- Update import test data to not include domains

### `test_integration.py`

- **`test_import_seed_data`**: Remove `stats["domains"] >= 10`, `len(graph["domains"]) >= 10`, `"domains" in node`
- **`test_expand_from_seed`**: Keep as-is (should now work with BFS + DeepSeek fix)
- **`test_sse_receives_expansion`**: Remove domains from test import data nodes
- **`test_graph_persists_restart`**: Remove domains from test import data nodes
- **`test_cluster_naming`**: Keep unchanged

## Verification

```bash
cd ~/projects/knowledge-tree
rm -f knowledge.db
pytest test_db.py test_generator.py test_server.py test_integration.py -v

# With API key for LLM-dependent tests:
DEEPSEEK_API_KEY="..." pytest test_integration.py -v
```
