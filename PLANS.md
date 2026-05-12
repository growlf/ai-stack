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
RETRIEVER_VAULT_PATH=/home/${STACK_USER}/obsidian
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

# Project Apostle — Self-Organizing Distributed AI Cluster

**Status**: Draft for discussion

---

## Vision

Every node running ai-stack is an autonomous member of a **peer-to-peer AI mesh**. Nodes can work entirely offline/solo. When they discover each other (on LAN, VPN, or tailnet), they authenticate, share capabilities, and pool their models into a unified routing layer. Each node becomes a gateway to the collective intelligence of the group.

Laptops come and go. Desktops stay. Servers anchor. The mesh adapts.

---

## Core principles

1. **Autonomy first** — Every node works perfectly alone. Joining a mesh is additive, never required.
2. **Zero central orchestration** — No single source of truth. No ansible master. No DJ. Every node is equal.
3. **Hardware-aware** — Models are matched to the node's capabilities. A laptop doesn't pull a 27B model.
4. **Gossip protocol for discovery** — Nodes announce themselves. Others hear it and decide whether to pair.
5. **Trust-on-first-use authentication** — Nodes authenticate via pre-shared key or interactive approval.
6. **Blob-level transfer** — Only missing SHA256 blobs are transferred between peers via Ollama's built-in `/api/blobs/` HTTP endpoint. No SSH, no NFS, no rsync dependency between nodes.
7. **Unified routing** — Olla + LiteLLM configs update dynamically as the peer set changes.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Apostle Agent                         │
│  (runs on every node, lightweight Python service)        │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ Hardware │  │  Herd    │  │  Model   │  │ Config │ │
│  │ Oracle   │  │  Network │  │ Apostle  │  │ Writer │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───┬────┘ │
│       │             │             │             │      │
│       ▼             ▼             ▼             ▼      │
│  RAM/GPU/Disk   mDNS/peers/  decide what    update     │
│  introspection  heartbeat   to pull/push   olla.yaml  │
│                                               litellm  │
└─────────────────────────────────────────────────────────┘
         │               │               │
         ▼               ▼               ▼
    ┌────────┐     ┌──────────┐    ┌──────────┐
    │ Ollama │     │   Olla   │    │ LiteLLM  │
    │ :11434 │     │  :40114  │    │  :4000   │
    └────────┘     └──────────┘    └──────────┘
```

### Components

#### 1. Hardware Oracle

Introspects the node and produces a capability fingerprint:

```yaml
node_id: node-abc123
capabilities:
  ram_gb: 31
  ram_available_gb: 22
  gpu:
    vendor: intel
    model: Arc Graphics / Iris Xe
    vram_gb: 0           # shared memory, no dedicated VRAM
  disk_available_gb: 140
  ollama_version: 0.20.7
  is_laptop: false
  subnet: 192.168.1.x    # for latency-based peer ranking
```

#### 2. Herd Network

Discovers and tracks peers:
- **mDNS** (avahi-browse) for LAN discovery
- **Seed list** from `.env` `OLLAMA_REMOTE_*` for VPN/WAN peers
- **Heartbeats** — periodic health check; remove peers after N misses
- **Authentication** — optional: sign announcements with a cluster key, or interactive approval

Peer state table:

| Peer | IP | Models | Health | Last seen | Latency |
|---|---|---|---|---|---|
| server-1 | 192.168.1.10 | [8 models] | healthy | 2s | 1ms |
| desktop-2 | 192.168.1.20 | [5 models] | healthy | 3s | 2ms |
| desktop-3 | 192.168.1.21 | [2 models] | healthy | 4s | 2ms |
| laptop-1 | 10.8.0.5 | [1 model] | stale | 5m | — |

#### 3. Model Apostle

The intelligence layer. For each model in the desired catalog:

```
┌──────────────────────────────────────────┐
│            Decision Engine                │
│                                          │
│  1. Load hardware fingerprint             │
│  2. Load model catalog (models.yaml)      │
│  3. Determine RAM budget (e.g. 70%)       │
│  4. Filter models that fit                │
│  5. Add must-have models (nomic-embed)   │
│  6. Sort by priority                     │
│  7. Diff against local inventory          │
│  8. For each missing model:              │
│     a. Query peers for the model          │
│     b. Rank peers by latency + load       │
│     c. Fetch blobs via GET /api/blobs/    │
│     d. Write manifest locally             │
│     e. Register with Ollama               │
└──────────────────────────────────────────┘
```

**The key transfer mechanism**: Ollama serves individual model blobs over plain HTTP at `GET /api/blobs/sha256-<digest>`. The apostle downloads only the blobs it's missing from the fastest peer that has them. Since blobs are content-addressed, multiple peers can serve different blobs in parallel.

```python
# Pseudocode
def acquire_model(model_name, manifest, peers):
    for layer in manifest["layers"]:
        digest = layer["digest"]
        local_path = f"/path/to/blobs/{digest.replace(':', '-')}"
        if os.path.exists(local_path):
            continue
        for peer in sorted(peers, key=latency):
            try:
                resp = http_get(f"http://{peer}:11434/api/blobs/{digest}")
                write(local_path, resp.body)
                break
            except:
                continue
```

#### 4. Config Writer

Dynamically generates and reloads:
- **Olla config** (`proxy/olla.yaml`) — adds/removes upstream Ollama endpoints as peers join/leave
- **LiteLLM config** (`proxy/litellm_config.yaml`) — keeps cloud model routing static but could update fallbacks
- **Smart Router config** — updates available model map
- Reloads via API call to Olla (`POST /internal/reload`) or SIGHUP

---

## The Apostle API

Each node exposes a lightweight HTTP endpoint (could be a new container or sidecar):

```
GET  /apostle/v1/capabilities    → hardware fingerprint
GET  /apostle/v1/peers           → known peer list
POST /apostle/v1/announce        → peer sends its capabilities
GET  /apostle/v1/models          → desired vs actual model inventory
POST /apostle/v1/sync            → trigger model reconciliation
```

This allows nodes to query each other directly, or a CLI tool to inspect cluster state:

```bash
apostle peers              # list known peers
apostle models             # show model inventory vs desired
apostle sync               # trigger model reconciliation
apostle status             # cluster health overview
```

---

## Model catalog (`scripts/models.yaml`)

The authoritative model catalog is at `scripts/models.yaml` — a YAML file listing ~16 models with their RAM requirements, disk footprint, tool-support flag, priority, and role. Kept as a standalone file so the Apostle script can load it without parsing this document.

Key fields per model entry:
- `name` — Ollama model tag (e.g. `qwen3.5:14b`)
- `min_ram_gb` — minimum RAM required to run the model
- `disk_gb` — disk space the model occupies
- `tools` — whether the model supports OpenAI-style function calling
- `priority` — critical > high > medium > low (for budget-aware selection)
- `role` — semantic category (embeddings, general, code, reasoning, etc.)

The Apostle's selection engine uses these fields combined with hardware introspection to choose which models belong on a given node.

---

## Node personality profiles

Rather than a one-size-fits-all model list, nodes categorize themselves:

| Profile | Criteria | Model selection strategy |
|---|---|---|
| `server` | >32GB RAM, always-on, no battery | Full catalog that fits |
| `desktop` | 16-32GB RAM, always-on | Mid-sized models, no 32B+ |
| `laptop` | 8-16GB RAM, battery, comes and goes | Small models only (7B max) |
| `ultra-light` | <8GB RAM, ARM/mobile | Tiny models (3B max) |
| `custom` | User-defined explicit list | Specified models only |

Determined automatically but overridable in config.

---

## Security model

| Aspect | Approach |
|---|---|
| Discovery | mDNS (link-local only) + optional seed list |
| Authentication | Cluster shared secret in `.env` or interactive pair approval |
| Blob transfer | Plain HTTP on Ollama port (already internal-only) |
| Config updates | Node only modifies its own configs |
| Join/leave | Soft — no formal leave; peers expire after heartbeat timeout |

---

## Implementation phases

### Phase 1: Apostle core ✓
- [x] `scripts/models.yaml` — model catalog with RAM/priority/role per model
- [x] `scripts/apostle.py` — single script: introspection → peer discovery → model reconciliation
- [x] Hardware introspection (RAM, GPU, disk, laptop detection, Ollama version)
- [x] Model selection engine (filters by RAM budget + profile: server/desktop/laptop/ultra-light)
- [x] Peer discovery from `.env` `OLLAMA_REMOTE_*` entries
- [x] HTTP blob fetch from peers (`GET /api/blobs/<digest>`)
- [x] SSH-based manifest reading for blob discovery
- [x] CLI: `apostle status`, `apostle sync`, `apostle peers`, `apostle catalog`

### Phase 2: Dynamic config
- [ ] Auto-generate Olla config from peer set
- [ ] Hot-reload Olla config without restart
- [ ] Auto-generate smart router model map

### Phase 3: Persistent daemon
- [ ] Apostle runs as a sidecar service (Docker or systemd)
- [ ] Heartbeat monitoring — detect peer departures
- [ ] Periodic model reconciliation (cron-like but driven by events)
- [ ] Apostle API endpoint (`/apostle/v1/*`)

### Phase 4: Laptop mobility
- [ ] Graceful peer departure (laptop closes lid → peers remove it)
- [ ] Re-join fast path (laptop resumes → quick hello)
- [ ] Battery-aware model loading (don't pull big models on battery)

### Phase 5: Full mesh intelligence
- [ ] Request routing based on model location (Olla already does this)
- [ ] Load-aware peer selection for blob downloads
- [ ] Predictive model pre-loading (based on usage patterns)
- [ ] Peer health scoring and graceful degradation

---

## Open questions for discussion

1. **Should the Apostle be a sidecar container or a systemd service?** Sidecar is consistent with the stack; systemd survives Docker daemon restarts.
2. **How does a laptop announce departure?** SHUTDOWN signal? Or just heartbeat timeout?
3. **Should peer-to-peer blob transfer use compression?** Models are already compressed; probably not.
4. **What's the auth UX for first-time pair?** Shared secret in `.env`? QR code? Both?
5. **How does the REST API port get chosen?** Fixed port? Dynamic from `.env`?

---

This is a living document. As we build, we update.
