# Knowledge Tree — Implementation Chunks

Each chunk is a self-contained specification with full context, interface contracts, and test criteria. A fresh Claude Code session can implement any chunk by reading only its document (plus the documents of its dependencies for interface reference).

## Dependency Graph

```
Chunk 1: db.py          ──► Chunk 3: server.py ──► Chunk 5: index.html
Chunk 2: generator.py   ──┘                    ──► Chunk 6: seed_data + integration
                              Chunk 4: force-worker.js ──┘
```

**Chunks 1, 2, and 4 can be implemented in parallel** (no interdependencies).

## Chunks

| # | File | Implements | Tests | Dependencies |
|---|------|-----------|-------|-------------|
| 1 | [chunk-1-database.md](chunk-1-database.md) | `db.py` — SQLite graph storage | 14 pytest unit tests | stdlib only |
| 2 | [chunk-2-generator.md](chunk-2-generator.md) | `generator.py` — multi-backend LLM caller | 12 pytest tests (mocked) | openai, anthropic, google-genai |
| 3 | [chunk-3-server.md](chunk-3-server.md) | `server.py` — aiohttp REST + SSE | 13 pytest API tests | aiohttp, Chunks 1+2 |
| 4 | [chunk-4-force-worker.md](chunk-4-force-worker.md) | `force-worker.js` — d3-force Web Worker | visual test page | d3-force (CDN) |
| 5 | [chunk-5-frontend.md](chunk-5-frontend.md) | `index.html` — Deck.gl frontend | 14 manual checks | Deck.gl (CDN), Chunks 3+4 |
| 6 | [chunk-6-integration.md](chunk-6-integration.md) | `seed_data.json` + e2e tests | 5 integration tests | All chunks |

## How to Hand Off a Chunk

Paste this to a fresh Claude Code session:

> Read `~/projects/knowledge-tree/chunks/chunk-N-<name>.md` and implement everything it specifies. Run the tests and make them pass.

For chunks with dependencies, the dependent chunk specs contain the full interface contracts needed — no need to read the implementation code unless debugging.
