# PLANS — ai-stack simplification and retriever service

## Status: Approved for implementation

---

## Summary

Remove Open WebUI, Pipelines, Open Terminal, and Khoj from the stack.
Replace Khoj's Obsidian RAG with a lightweight, API-only retrieval service.
Add an mDNS-based herd discovery script for other Ollama nodes on the LAN.
OpenCode becomes the primary AI interface (CLI + embedded in Obsidian via plugin).

---

## Motivation

- OpenCode provides a better chat/assistant experience than Open WebUI
- OpenCode is embedded directly in Obsidian via a custom plugin
- Khoj + PostgreSQL is heavy for a single-user RAG backend
- No need for multi-service infrastructure (pipelines, terminal, web UI) when OpenCode handles everything

---

## Architecture after changes

```
Notice: Server-Sent Events (may require SSE to work)

OpenCode (CLI + Obsidian sidebar plugin)
  |
  |--- tool: retriever :42000 (vault RAG)
  |       FastAPI + sqlite-vec + watchdog
  |       hybrid search: BM25 (FTS5) + vector (sqlite-vec)
  |       embeds via Olla -> ollama-arc (nomic-embed-text)
  |       vault mounted :ro at /vault
  |
  |--- provider: Olla :40114 (unified LLM router)
  |       |--- ollama-arc :11434 (Intel Arc iGPU, local)
  |       |--- litellm :4000 (Claude, Gemini, cloud)
  |       |--- OLLAMA_REMOTE_* nodes (LAN, optional)
  |
  |--- provider: litellm :4000 (direct, optional)

discoverer (systemd timer): mDNS scan -> updates Olla config + OpenCode providers
```

---

## Services to remove

| Service | Reason | Impact |
|---|---|---|
| `open-webui` | Replaced by OpenCode | Lose chat history volume, admin panel |
| `pipelines` | Function pipelines are OpenWebUI-only | smart_model_router.py no longer needed |
| `open-terminal` | Browser terminal, only integrated in WebUI | No loss |
| `khoj` | Replaced by retriever service | Lose Khoj web UI, Obsidian plugin, PostgreSQL |
| `khoj-db` | Replaced by sqlite-vec (file-based) | Volume deleted |

## Services to keep

| Service | Reason |
|---|---|
| `ollama-arc` | Local GPU inference for all services |
| `litellm` | Cloud API gateway for Claude, Gemini |
| `olla` | Unified router — retriever + OpenCode both point here |

## New: retriever service

### Design

- **Container**: `retriever` in docker-compose.yml
- **Base image**: `python:3.12-slim`
- **Framework**: FastAPI
- **Vector store**: sqlite-vec (SQLite extension for vector search)
- **Keyword search**: SQLite FTS5
- **File watching**: watchdog (inotify)
- **Embedding**: POST to Olla -> ollama-arc (nomic-embed-text, 768-dim)
- **Port**: 42000

### API

```
POST /search
  {"query": "what did I write about networking?", "top_k": 10}
  -> {"results": [{"path": "...", "content": "...", "score": 0.92}, ...]}

POST /index
  {"paths": ["note1.md", "subdir/note2.md"]}
  -> {"indexed": 2, "skipped": 5}

GET /health
  -> {"status": "ok", "indexed_files": 1240, "vault_watching": true}
```

### Indexing strategy

- On startup: full scan of `/vault` (mounted :ro from host)
- Incremental: watchdog watches for file create/modify/delete events
- Chunking: markdown-aware splitter (respects `#` headings, `---` thematic breaks)
- Hybrid search: vector similarity (cosine) + keyword relevance (BM25 via FTS5)
- Result fusion: reciprocal rank fusion (RRF) to combine both scores

## New: discoverer

### Design

- **Script**: `scripts/discover-herd.sh` (bash)
- **Trigger**: systemd timer or cron (every 5 min)
- **Mechanism**: `avahi-browse` for `_ollama._tcp` mDNS service type
- **Output**: Writes discovered nodes to:
  - `proxy/olla.yaml` (via `generate-olla-config.sh`)
  - OpenCode provider config (`.opencode/providers.yaml`)
- **Fallback**: also scan common ports (11434, 11435) on local subnet

---

## File changes

### New files

| File | Purpose |
|---|---|
| `retriever/Dockerfile` | Multi-stage Python build |
| `retriever/main.py` | FastAPI app, route handlers |
| `retriever/indexer.py` | Vault scanner, watchdog, chunking |
| `retriever/search.py` | Hybrid search, FTS5 + sqlite-vec, RRF fusion |
| `retriever/requirements.txt` | fastapi, uvicorn, watchdog, sqlite-vec, httpx |
| `scripts/discover-herd.sh` | mDNS discovery + OpenCode/Olla config gen |

### Modified files

| File | Change |
|---|---|
| `docker-compose.yml` | Remove open-webui, pipelines, open-terminal, khoj, khoj-db. Add retriever. Drop external volumes. |
| `.env.example` | Strip WEBUI_*, PIPELINES_*, OPEN_TERMINAL_*, KHOJ_*, COUCHDB_*. Add RETRIEVER_*. |
| `.env` | Match .env.example changes |
| `AGENTS.md` | Reflect new architecture and developer commands |
| `start.sh` | Remove post-install hint; add retriever health check |

### Deleted files

| File | Reason |
|---|---|
| `post-install.sh` | Entirely targets Open WebUI API |
| `pipelines/` | Open WebUI-specific |
| `tools/` | Open WebUI-specific |
| `khoj-sync/` | Empty, never implemented |
| `docs/post-install.md` | Describes WebUI admin panel steps |

---

## Environment variables

### Removed

```
WEBUI_PORT, WEBUI_NAME, WEBUI_SECRET_KEY
PIPELINES_PORT, PIPELINES_API_KEY
OPEN_TERMINAL_PORT, OPEN_TERMINAL_API_KEY
KHOJ_PORT, KHOJ_ADMIN_EMAIL, KHOJ_ADMIN_PASSWORD
KHOJ_DJANGO_SECRET_KEY, KHOJ_DB_PASSWORD
KHOJ_NO_AUTH, OBSIDIAN_VAULT_PATH
COUCHDB_URL, COUCHDB_DB, COUCHDB_USER, COUCHDB_PASSWORD
KHOJ_SYNC_SKIP_INITIAL, KHOJ_SYNC_LOG_LEVEL
```

### Added

```
RETRIEVER_PORT=42000
RETRIEVER_VAULT_PATH=/home/netyeti/obsidian
RETRIEVER_EMBED_MODEL=nomic-embed-text
RETRIEVER_CHUNK_SIZE=512
RETRIEVER_CHUNK_OVERLAP=64
```

### Kept (unchanged)

```
STACK_USER, OLLAMA_DATA, GPU_CARD, GPU_RENDER
OLLAMA_PORT, LITELLM_PORT, LITELLM_MASTER_KEY
OLLA_PORT, ANTHROPIC_API_KEY, GEMINI_API_KEY
OLLAMA_REMOTE_*, MODELS_TO_PULL
OLLA_ENGINE, OLLA_LOAD_BALANCER, OLLA_REQUEST_LOGGING
```

---

## Developer commands (updated)

```bash
docker compose up -d                    # start: ollama-arc, litellm, olla, retriever
docker compose logs -f retriever        # watch vault indexing
curl localhost:42000/health             # check retriever status
bash scripts/discover-herd.sh           # manual discover + config
```

---

## Rollback

If the new setup doesn't work:
1. `git checkout` the deleted files from git
2. Revert docker-compose.yml to previous version
3. Recreate the `open-webui` external volume
4. Re-pull previous `.env`
