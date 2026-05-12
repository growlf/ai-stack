# AGENTS.md — ai-stack

This is a Docker Compose-based AI stack managed via systemd. The stack provides local LLM inference (ollama), cloud API routing (LiteLLM), unified routing/load balancing (Olla), and Obsidian vault RAG (retriever). The primary AI interface is **OpenCode** (CLI + Obsidian sidebar plugin).

## Developer commands

```bash
# Start / stop / restart (via systemd, preferred)
sudo systemctl start|stop|restart ai-stack.service

# Direct docker compose (for testing, not persistent)
# NOTE: start.sh auto-resolves <vaultwarden:...> placeholders first
./start.sh            # foreground
./start.sh -d         # detached
./start.sh down       # tear down

# Regenerate Olla config after changing .env OLLAMA_REMOTE_* entries
./scripts/generate-olla-config.sh

# Resolve Bitwarden/VaultWarden placeholders in .env (auto-runs in start.sh)
./scripts/resolve-vaultwarden.sh              # resolve in-place
./scripts/resolve-vaultwarden.sh --dry-run    # preview only

# Discover other Ollama nodes on the LAN
./scripts/discover-herd.sh              # prompt before writing
./scripts/discover-herd.sh --apply      # write without prompt
./scripts/discover-herd.sh --dry-run    # scan only

# Sync models across cluster nodes (LAN speed if SSH keys deployed)
./scripts/sync-models.sh                          # sync all nodes
./scripts/sync-models.sh 10.10.0.212              # sync one node
./scripts/sync-models.sh --dry-run                # preview only
./scripts/sync-models.sh --ssh-key ~/.ssh/id_ed25519  # with SSH key

# Self-aware model apostle (hardware-aware peer-to-peer sync)
./scripts/apostle.py status                       # cluster health + model inventory
./scripts/apostle.py sync                         # reconcile missing models from peers
./scripts/apostle.py peers                        # list known peers and their models
./scripts/apostle.py catalog                      # show which models fit this node

# Discover AI services across all networks (LAN + VPN)
./scripts/discover-network.sh                         # interactive
./scripts/discover-network.sh 10.10.0.201:11434       # seed(s) as args, prompts
./scripts/discover-network.sh --apply                 # add all discovered
./scripts/discover-network.sh --dry-run               # scan only

# Check retriever status
curl localhost:42000/health

# Search the vault
curl -X POST localhost:42000/search -H 'Content-Type: application/json' \
  -d '{"query":"what did I write about networking?"}'

# Force vault reindex
curl -X POST localhost:42000/reindex

# CI validation (run locally before push)
docker compose config --quiet
shellcheck -s bash scripts/*.sh install.sh

# GPU pre-flight check
./scripts/check-arc-gpu.sh
```

## Architecture

All traffic flows through **Olla** (port 40114) as the unified LLM router:

```
OpenCode (CLI + Obsidian plugin)
  ├── tool: retriever :42000  →  sqlite-vec + FTS5 hybrid search over vault
  ├── provider: Olla :40115   →  Smart Router (auto-selects local model)
  │                           →  ollama :11434 (local LLM)
  │                           →  OLLAMA_REMOTE_* nodes (LAN, optional)
  └── provider: LiteLLM :4000 →  Claude (Anthropic), Gemini (Google)
```

### Service responsibilities

| Directory/File | Purpose |
|---|---|
| `docker-compose.yml` | Core stack: ollama, litellm, olla, router, retriever |
| `install.sh` | Preflight → create volumes → install systemd → start stack → pull models (prompts for Bitwarden setup) |
| `retriever/` | Obsidian vault RAG: FastAPI + sqlite-vec + watchdog. Hybrid search via FTS5 + vector embeddings. |
| `scripts/generate-olla-config.sh` | Reads `OLLAMA_REMOTE_*` from `.env` → writes `proxy/olla.yaml` |
| `scripts/discover-herd.sh` | mDNS + subnet scan for other Ollama nodes on LAN |
| `scripts/check-arc-gpu.sh` | GPU pre-flight: detects card0/card1 drift, updates `.env`, used as `ExecStartPre` |
| `scripts/resolve-vaultwarden.sh` | Resolves `<vaultwarden:path>` placeholders in `.env` via `bw` CLI |
| `scripts/apostle.py` | Self-aware model apostle — hardware-aware peer-to-peer sync (status/sync/peers/catalog) |
| `scripts/models.yaml` | Model catalog with 16 entries, RAM/disk/tools/priority/role metadata |
| `PLANS.md` | Project Apostle full plan — architecture, phases, design decisions |
| `router/` | Smart Model Router: content-based model selection between OpenCode and Olla |
| `proxy/litellm_config.yaml` | Static LiteLLM model registry (Claude, Gemini models) |

## Key conventions

- **`.env` is the single source of truth** for LAN node addresses, GPU paths, API keys, and ports. Scripts parse `OLLAMA_REMOTE_*` vars — do not hardcode IPs in compose or scripts.
- **`proxy/olla.yaml` is auto-generated** — never edit directly. Regenerate via `scripts/generate-olla-config.sh`. It's in `.gitignore`.
- **GPU card node drifts** (`card0` vs `card1`) on Meteor Lake reboots. `renderD128` is stable. `check-arc-gpu.sh` detects and corrects via systemd `ExecStartPre`.
- **Retriever volume** is managed by compose (`retriever-data`). Contains the sqlite-vec database with embeddings and FTS5 index.
- **`OLLAMA_KEEP_ALIVE=-1`** keeps models resident in shared system RAM (Intel iGPU uses shared memory, not dedicated VRAM).
- **VaultWarden integration is optional** — `install.sh` prompts to set it up. If declined, `.env` stores API keys in plaintext (standard practice for local-only stacks).
- **Never commit secrets** — `.env` is in `.gitignore`. The installer generates `LITELLM_MASTER_KEY` and stores it in Bitwarden automatically. Anthropic/Gemini keys are stored as `<vaultwarden:...>` placeholders and resolved at runtime.

## RAG (retriever)

The retriever service replaces Khoj + PostgreSQL with a lightweight, API-only service:

- **Vector store**: sqlite-vec (embedded SQLite extension, file-based, no separate DB)
- **Keyword search**: SQLite FTS5 (BM25 scoring)
- **Hybrid search**: Reciprocal Rank Fusion (RRF) combining vector + keyword results
- **Embeddings**: `nomic-embed-text` via Olla → ollama
- **Indexing**: Full scan on startup, then watchdog (inotify) for live changes
- **API**: `POST /search`, `POST /reindex`, `GET /health`

Configuration via `.env`:
```
RETRIEVER_PORT=42000
RETRIEVER_VAULT_PATH=/home/user/obsidian
RETRIEVER_EMBED_MODEL=nomic-embed-text
RETRIEVER_CHUNK_SIZE=512
RETRIEVER_CHUNK_OVERLAP=64
```

## Testing

- `pytest.ini` sets `testpaths = tests` and `asyncio_mode = auto`
- Python test deps: `requirements-dev.txt` (pytest, pytest-asyncio, httpx, pydantic)
- CI runs: `docker compose config --quiet`, shellcheck, `.env.example` credential scan

## CI / Release

- **CI** (`.github/workflows/ci.yml`): validates compose syntax, shellchecks scripts, scans `.env.example` for leaked credentials
- **Release** (`.github/workflows/release.yml`): triggered on `v*.*.*` tags, extracts notes from `CHANGELOG.md`
- Branch: `main`

## OpenCode tools

The project includes two custom tools for Obsidian vault search:

- **`vault-search`** — search the entire vault for notes matching a query
- **`vault-search_per_source`** — search within a specific file or subdirectory

Both tools call the retriever service (`:42000`) and return file paths, content snippets, and scores. Use them when asked to find information in notes.

## Gotchas

- LiteLLM healthcheck URL is `/health/liveness` (not "liveliness")
- `install.sh` model pull is interactive (prompts y/N) — non-headless
- Retriever depends on Olla for embeddings — ensure Olla is healthy before retriever starts
- Vault is mounted read-only (`:ro`) — retriever never modifies notes
- `discover-herd.sh` requires `avahi-daemon` running on the host for mDNS discovery
- `bw login --apikey` is incompatible with self-hosted VaultWarden (no `userDecryptionOptions` in response). Use interactive `bw login` or an existing unlocked session for `resolve-vaultwarden.sh`.
- `install.sh` auto-generates the `LITELLM_MASTER_KEY`, creates a `litellm-master-key` item in your Bitwarden vault, and writes a `<vaultwarden:...>` placeholder to `.env`. For `anthropic-api-key` and `gemini-api-key`, you must create those items manually via the Bitwarden web vault.
