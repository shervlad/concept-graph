# Chunk 12: Editable System Prompt from Interface

## Goal

Let the user edit the system prompt used for all LLM calls from the sidebar UI. Persists in the app state (resets on server restart).

**Prerequisite:** Chunks 7–11

## Changes

### `index.html`

Add a collapsible section in the sidebar, after the Backend select and before Expansion controls:

```html
<div class="control-group">
  <label>System Prompt</label>
  <textarea id="system-prompt" rows="3" style="width:100%;font-size:12px;resize:vertical;">You list related knowledge concepts. Output ONLY valid JSON.</textarea>
</div>
```

All API calls that use the LLM (`/api/expand`, `/api/describe`, `/api/cluster-names`) include the current textarea value as `"system_prompt"` in the POST body.

### `server.py`

Each handler that calls generator functions reads `system_prompt` from the request body and passes it through:

- `handle_expand`: `body.get("system_prompt")` → pass to `expansion_loop`
- `handle_describe`: `body.get("system_prompt")` → pass to `describe_node`
- `handle_cluster_names`: `body.get("system_prompt")` → pass to `name_clusters`

If `system_prompt` is not provided or empty, the generator uses its default.

### `generator.py`

Add an optional `system_prompt` parameter to all functions that call the LLM:

- `call_llm(prompt, backend, system_prompt=None)` — uses `system_prompt or SYSTEM_PROMPT`
- `expand_once(focal, db, backend, count, system_prompt=None)` — passes through to `call_llm`
- `expansion_loop(seed, db, backend, callback, max_rounds, count, system_prompt=None)` — passes through to `expand_once`
- `describe_node(name, year, backend, system_prompt=None)` — passes through to `call_llm`
- `name_clusters(clusters, backend, system_prompt=None)` — passes through to `call_llm`

The default `SYSTEM_PROMPT` constant stays as the fallback.

## Verification

```bash
cd ~/projects/knowledge-tree
python server.py &
# Open http://localhost:8080
# 1. See default system prompt in textarea
# 2. Edit it to something like "You are a biology expert. Output ONLY valid JSON."
# 3. Run expansion — verify LLM output is biased toward biology concepts
# 4. Clear the textarea — verify expansion still works (uses default)
```
