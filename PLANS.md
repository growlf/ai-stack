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

---

# Plan: Multi-system install with cluster roles

## Status: Proposal — pre-approval

---

## Summary

Add three installation modes to `install.sh`: **Start Cluster**, **Join Cluster**, and **Standalone**. The installer becomes re-runnable so any node can change its role later. `Join` mode scans the LAN for existing clusters and offers a selection menu.

## Motivation

- Current install assumes a single, self-contained node
- Users with multiple machines (homelab, office LAN) manually copy `OLLAMA_REMOTE_*` entries between `.env` files
- No standard way to bootstrap a multi-node cluster or join an existing one
- The `discover-network.sh` script exists but is manual and post-install only
- Re-running the installer to change roles should be safe and guided

## Design

### Core concept: `CLUSTER_ROLE` in `.env`

A new `.env` variable tracks the node's role:

```
CLUSTER_ROLE=standalone|seed|member
CLUSTER_NAME=ai-cluster       # human label
CLUSTER_ID=<uuid>             # generated once per seed, stable reference
CLUSTER_NODES=[]              # tracked on seed only
```

- **standalone**: current behavior — no cluster participation (default)
- **seed**: first node — originates the cluster, hosts the node registry
- **member**: joins an existing cluster — appears in seed's registry

### Mode selection flow (re-runnable)

```
install.sh
├── [.env not found] → preflight error (as today)
├── [.env found, no CLUSTER_ROLE] → prompt: Start Cluster | Join Cluster | Standalone
│   ├── Start Cluster → writes CLUSTER_ROLE=seed + CLUSTER_ID + CLUSTER_NAME
│   ├── Join Cluster → scan LAN → select cluster → writes CLUSTER_ROLE=member + remote entries
│   └── Standalone → writes CLUSTER_ROLE=standalone, proceeds as today
├── [.env found, CLUSTER_ROLE exists] → prompt: Keep role or change?
│   ├── Keep → run normal install (idempotent)
│   └── Change → re-run the mode selection above
└── (rest of installer: preflight, volumes, systemd, models, opencode)
```

### Join Cluster — LAN discovery mechanism

Discovery uses the existing `scripts/discover-network.sh` logic:
1. Scan LAN subnet (via `ip route`) for hosts on port 11434 (Ollama)
2. For each reachable host, query `http://host:11434/api/tags` and check `ai-stack.aio-config` model tag (a marker model seed nodes pull)
3. Seeds also expose `http://host:11434/.ai-stack-manifest` → JSON with `{cluster_id, cluster_name, node_count, models}`
4. Present a numbered menu of discovered clusters
5. User selects → installer writes `OLLAMA_REMOTE_<hostname>=http://selected-host:11434` to `.env` and registers this node's Ollama with the seed

Alternative/simpler approach (the seed runs no extra HTTP endpoint):
1. Scan LAN for Ollama hosts via discover-network.sh
2. Detect which ones have `CLUSTER_ROLE=seed` by checking if the host itself has an ai-stack running (check for `.env` marker via SSH or the Olla health endpoint)
3. This is too invasive — skip.

**Chosen approach**: seed pulls a well-known model tag `ai-stack-cluster:latest` (a tiny placeholder ~1MB). Joiners scan for hosts with this model. This is zero-infrastructure — no extra endpoints, no SSH.

Alternatively, Olla's `/internal/status/endpoints` endpoint exposed on port 40114 already lists configured remotes. If a seed's Olla is reachable (port 40114), joiners can check it for `cluster` metadata. But we can also add a lightweight `GET /cluster/manifest` to Olla (if we extend it) or to a separate sidecar.

**Simplest v1 approach**: a small shell function in `install.sh` that:
1. Scans the local subnet on port 11434 (via `/dev/tcp` or `nmap` if available)
2. Tries `wget -q -O - http://$host:$port/api/tags | grep -q ai-stack-cluster` 
3. Shows matches as a menu
4. No infra changes — just uses Ollama's existing API

### Start Cluster — seed node registration

1. Generate `CLUSTER_ID` via `uuidgen`
2. Prompt for `CLUSTER_NAME` (default: hostname)
3. Pull `ai-stack-cluster:latest` marker model so joiners can detect this node
4. Write `CLUSTER_ROLE=seed` + `CLUSTER_ID` + `CLUSTER_NAME` to `.env`
5. Rest of install proceeds normally

### Re-runnable role changes

A new `scripts/change-cluster-role.sh` script or inline function:
1. Reads current `CLUSTER_ROLE`
2. Offers migration path:
   - **seed → standalone**: Remove marker model, stop advertising, keep remotes as-is (optional)
   - **seed → member**: Demote to member, join another cluster
   - **member → seed**: Promote to seed, pull marker, existing remotes become cluster nodes
   - **member → standalone**: Remove remote entries, keep local
   - **standalone → seed/member**: As above

### File changes

| File | Change |
|---|---|
| `install.sh` | Top-level mode selection menu; new functions for scan, join, seed init; re-run detection |
| `.env.example` | Add `CLUSTER_ROLE`, `CLUSTER_NAME`, `CLUSTER_ID` (commented out w/ defaults) |
| `scripts/change-cluster-role.sh` | **New** — role migration script invoked by re-running install or standalone |
| `scripts/discover-network.sh` | Minor: export `scan_lan_ollama()` as a library function so install.sh can source it |
| `PLANS.md` | This plan |

### Environment variables (new)

```
# ── Cluster mode ──────────────────────────────────────────────────────
# CLUSTER_ROLE=standalone       # standalone|seed|member
# CLUSTER_NAME=                 # (optional) human label
# CLUSTER_ID=                   # auto-generated on seed init
```

### Developer commands (supplement to AGENTS.md)

```bash
# Re-run installer to change role
./install.sh

# Or use the standalone script
./scripts/change-cluster-role.sh
```

### Rollback

1. Set `CLUSTER_ROLE=standalone` in `.env`
2. Remove marker model: `docker exec ollama-arc ollama rm ai-stack-cluster:latest`
3. Remove any auto-added `OLLAMA_REMOTE_*` entries from `.env`
4. Re-run `./scripts/generate-olla-config.sh` and restart the stack
