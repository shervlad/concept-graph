# Knowledge Tree

Interactive knowledge graph visualization powered by LLMs. Explore how concepts connect across history — from ancient counting to modern AI.

## Quick Start

```bash
pip install -r requirements.txt

# Configure at least one LLM backend
export OPENAI_API_KEY=sk-...
# or: ANTHROPIC_API_KEY, GOOGLE_API_KEY, DEEPSEEK_API_KEY, XAI_API_KEY

python server.py
# Open http://localhost:8080
```

## How It Works

1. **Type a seed concept** — e.g. "God", "Mathematics", "DNA" — and hit Start
2. **Expand** — the system uses BFS to discover related concepts via the LLM; each round expands one node and generates related concepts with names, years, and relatedness weights
3. **Visualize** — nodes are positioned by a d3-force physics simulation in a Web Worker and rendered with Deck.gl; color maps year to hue (red = ancient, purple = modern)
4. **Describe** — click any node to auto-generate a one-sentence description via the LLM
5. **Cluster** — the force simulation groups related nodes; request LLM-generated cluster labels on demand
6. **Import seed data** — optionally import `seed_data.json` (127 historical knowledge concepts) as a starting point

## Architecture

```
index.html          ← Deck.gl frontend (single file, no build step)
force-worker.js     ← d3-force physics in a Web Worker
server.py           ← aiohttp REST API + SSE streaming
generator.py        ← Multi-backend LLM expansion engine (BFS)
db.py               ← SQLite graph storage (WAL mode, thread-safe)
seed_data.json      ← Initial dataset (127 nodes, 177 edges)
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/graph?limit=0&center=id` | Fetch graph (nodes + edges); limit=0 returns all |
| GET | `/api/stats` | Node and edge counts |
| GET | `/api/backends` | Available LLM backends and status |
| GET | `/api/stream` | SSE event stream for real-time updates |
| POST | `/api/expand` | Start BFS expansion `{seed, backend, rounds, count, system_prompt, user_prompt}` |
| POST | `/api/stop` | Cancel running expansion |
| POST | `/api/describe` | Generate node description `{id, backend, system_prompt}` |
| POST | `/api/cluster-names` | Name clusters via LLM `{clusters, backend, system_prompt}` |
| POST | `/api/import` | Bulk import `{nodes, links}` |
| POST | `/api/reset` | Clear all nodes and edges, stop expansion |

## LLM Backends

| Backend | Model | Env Variable |
|---------|-------|-------------|
| openai | gpt-4o | `OPENAI_API_KEY` |
| claude | claude-sonnet-4-20250514 | `ANTHROPIC_API_KEY` |
| gemini | gemini-2.0-flash | `GOOGLE_API_KEY` |
| deepseek | deepseek-chat | `DEEPSEEK_API_KEY` |
| grok | grok-3 | `XAI_API_KEY` |

## Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `PORT` | `8080` | Server port |
| `KNOWLEDGE_DB` | `knowledge.db` | SQLite database path |

Both the system prompt and user prompt template are editable from the UI. The user prompt supports `{N}` (concept count) and `{X}` (focal concept) placeholders.

## Tests

```bash
pytest -v                    # all tests
pytest test_db.py            # database layer
pytest test_generator.py     # LLM expansion logic (mocked)
pytest test_server.py        # API endpoints
pytest test_integration.py   # end-to-end (some require API keys)
```

## UI Controls

- **Search** — filter nodes by name
- **Click** node — pin position (yellow outline) and fetch LLM description; double-click to unpin
- **Hover** — tooltip with name, year, and description (if fetched)
- **Reset** — clear the entire graph and start fresh
- **Physics sliders** — tune charge, link distance, velocity decay, etc.
- **Render budget** — cap visible nodes for performance
- **System prompt** — optional LLM system instruction
- **User prompt** — editable expansion template with `{N}` and `{X}` placeholders
- **Concepts per round** — control how many concepts each BFS step generates
- **Save/Clear Settings** — persist all UI settings (sliders, prompts, backend) to localStorage
