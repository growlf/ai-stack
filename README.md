# ai-stack

> **A self-organizing, self-healing AI cluster for your own hardware.**

Run a full AI development stack — local LLMs, cloud augmentation, and a live cluster dashboard — on hardware you already own. No subscription, no token metering, no vendor lock-in.

[![CI](https://github.com/growlf/ai-stack/actions/workflows/ci.yml/badge.svg)](https://github.com/growlf/ai-stack/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Ollama](https://img.shields.io/badge/Ollama-local%20LLMs-black)](https://ollama.com)
[![OpenCode](https://img.shields.io/badge/OpenCode-AI%20IDE-purple)](https://opencode.ai)

---

## What is this?

ai-stack turns one or more machines into a **unified AI cluster** — local models where possible, cloud where needed, with a real-time dashboard so you can see exactly what's running where.

- **Local-first** — models run on your GPU/CPU; Ollama handles the inference
- **Cloud-augmented** — Claude, Gemini, or OpenAI via LiteLLM when local isn't enough
- **Smart routing** — an LLM-based classifier picks the right model for each request automatically
- **Self-organizing** — the Apostle agent keeps your cluster in sync, discovers peers, and heals itself

---

## The Vision — Project Apostle

Every node running ai-stack is a member of a **peer-to-peer AI mesh**. Nodes discover each other, share model catalogs, and distribute work based on hardware capability. When a model is missing, Apostle fetches it from the fastest peer — or pulls it from the registry if no peer has it yet. The cluster heals itself without any central coordinator.

```mermaid
graph TB
    subgraph Node A — Server 32GB
        OA[Ollama :11434]
        AP_A[Apostle :40116]
        RO_A[Router :40115]
        OL_A[Olla :40114]
    end

    subgraph Node B — Desktop 16GB
        OB[Ollama :11434]
        AP_B[Apostle :40116]
        OL_B[Olla :40114]
    end

    subgraph Node C — Laptop 8GB
        OC[Ollama :11434]
        AP_C[Apostle :40116]
    end

    subgraph Cloud
        CLAUDE[Claude / Gemini]
        LITELLM[LiteLLM :4000]
    end

    AP_A <-->|peer sync| AP_B
    AP_A <-->|peer sync| AP_C
    AP_B <-->|peer sync| AP_C

    OL_A -->|local| OA
    OL_A -->|remote| OL_B
    OL_A -->|cloud| LITELLM
    LITELLM --> CLAUDE

    RO_A -->|classify + route| OL_A
```

> Each node announces its capabilities. Missing models are fetched peer-to-peer. The cluster self-heals.

---

## Live Dashboards

Once running, open these in your browser:

| Dashboard | URL | What you see |
|-----------|-----|--------------|
| **Cluster** | `http://localhost:40116/ui` | D3 force graph — nodes, models, health, RAM per node |
| **Router** | `http://localhost:40115/gestalt/ui` | Live routing decisions, model usage, SSE feed |
| **LiteLLM** | `http://localhost:4000/ui` | Cloud model usage, costs, API keys |
| **Olla** | `http://localhost:40114/internal/status/endpoints` | Load balancer endpoint health |

### Apostle cluster graph

The cluster view (`/ui`) renders your entire AI cluster as a live force-directed graph. Each machine is a large circle — **green** for the local node, **blue** for healthy peers, **red** for unreachable ones. Every model running on a node orbits it as a smaller satellite node. Hover any circle to see RAM totals, available disk, GPU info, and the complete model inventory for that machine.

The graph redraws every 5 seconds via a live Server-Sent Events stream. A status indicator in the header shows whether the stream is active. When a new node joins or a model sync completes, the graph shifts and rebalances in front of you.

### Router gestalt view

The router view (`/gestalt/ui`) shows a live feed of every routing decision — which model was selected, which node it was sent to, and why — as it happens. Send a query from OpenCode and watch the entry appear within a second. The header counters tick up in real time: nodes in the cluster, models available, total requests routed.

> **To see these in action:** `docker compose up -d --build` → open `http://localhost:40116/ui` → send a query from OpenCode → watch the routing decision appear in the gestalt view simultaneously.

<!-- Screenshots: add docs/assets/cluster-dashboard.png and docs/assets/router-gestalt.png when available -->

---

## Architecture

```
 OpenCode (AI IDE + Obsidian plugin)
     │
     ├── tool ──▶  Retriever :42000      Obsidian vault RAG (hybrid BM25 + vector)
     │
     ├── provider ▶  Router :40115       LLM-based smart model classifier
     │                   │
     │               Olla :40114         Load balancer / unified LLM proxy
     │                   │
     │           ┌───────┴────────┐
     │         Ollama          LiteLLM
     │         :11434           :4000
     │       (local GPU)   (Claude/Gemini)
     │
     └── provider ▶  LiteLLM :4000       Direct cloud access (optional)

 Apostle :40116   Self-organizing cluster agent (runs on every node)
     │
     ├── /ui                D3 cluster dashboard + per-node health
     ├── /apostle/v1/status Hardware profile, RAM, GPU, model inventory
     ├── /apostle/v1/cluster Full cluster view with peer enrichment
     └── /apostle/v1/events SSE stream — live cluster snapshots
```

---

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/growlf/ai-stack.git
cd ai-stack
cp .env.example .env
nano .env   # set LITELLM_MASTER_KEY and your cloud API keys

# 2. Generate Olla proxy config
scripts/generate-olla-config.sh

# 3. Start the stack
docker compose up -d

# 4. Open the cluster dashboard
open http://localhost:40116/ui
```

For GPU acceleration:
```bash
# Intel Arc iGPU
docker compose -f docker-compose.yml -f docker-compose.arc.yml up -d

# NVIDIA GPU
docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d
```

**[Full install guide → docs/install.md](docs/install.md)**

---

## Services

| Service | Port | Purpose |
|---------|------|---------|
| **Ollama** | 11434 | Local LLM inference (CPU / CUDA / Arc) |
| **LiteLLM** | 4000 | Cloud API gateway (Claude, Gemini, OpenAI) |
| **Olla** | 40114 | LLM proxy + load balancer across nodes |
| **Retriever** | 42000 | Obsidian vault RAG — hybrid BM25 + vector search |
| **Router** | 40115 | Smart model classifier + routing dashboard |
| **Apostle** | 40116 | Cluster agent + observable dashboard |

---

## Smart Routing

The Router classifies every request and picks the right model automatically:

| Query type | Model selected |
|------------|---------------|
| Code / scripting | `qwen2.5-coder:14b` |
| Deep reasoning | `deepseek-r1:14b` |
| Long documents | `gemma3:12b` |
| Tool calling | `llama3.1:8b` |
| General chat | `qwen2.5:7b` |
| Cloud / complex | Claude / Gemini via LiteLLM |

The classifier uses a tiny local model (`qwen2.5:1.5b`) to categorize queries in ~100ms before routing. Cloud models are detected by name and passed through unchanged.

---

## Apostle — Self-Organizing Cluster

Apostle is a lightweight agent that runs on every node. It:

1. **Introspects** — reads RAM, GPU, disk, and Ollama version to build a hardware profile
2. **Selects** — chooses the right models from `scripts/models.yaml` based on available resources
3. **Discovers** — finds peer nodes via `OLLAMA_REMOTE_*` env vars (mDNS discovery coming in Phase 4)
4. **Syncs** — fetches missing model blobs from the fastest available peer; falls back to registry
5. **Heals** — background daemon detects gaps and fills them during low-resource windows
6. **Observes** — serves a live D3 cluster dashboard at `:40116/ui`

### Node profiles

| Profile | RAM | Models selected |
|---------|-----|----------------|
| `server` | ≥ 32 GB | Full catalog |
| `desktop` | 16–32 GB | Mid-size models |
| `laptop` | 8–16 GB | 7B and under |
| `ultra-light` | < 8 GB | 3B and under |

### Guardrails

Apostle checks before every pull:
- Available disk space (configurable threshold)
- Current system load (defers during high CPU/RAM usage)
- Cluster-wide download state (joins an in-progress pull rather than duplicating)
- Maintenance mode flag (`APOSTLE_MAINTENANCE=1`)

---

## Roadmap

- [x] **Phase 1** — Apostle core: hardware introspection, model selection, peer discovery, blob transfer, CLI
- [x] **Phase 2** — Observable: live D3 cluster dashboard, SSE events, HTTP API (`/apostle/v1/*`), router gestalt view
- [ ] **Phase 3** *(in progress)* — Self-healing daemon: background sync loop, startup healthcheck, peer-first acquisition, cluster-wide download deduplication, configurable guardrails
- [ ] **Phase 4** — Herd intelligence: load-aware routing weights, proactive model pre-loading based on cluster demand, coverage-aware distribution, mDNS peer discovery
- [ ] **Phase 5** — Full mesh: predictive scaling, laptop mobility (graceful departure + re-join), battery-aware scheduling, model migration between nodes

---

## Multi-Machine Setup

Add remote Ollama nodes to your `.env`:

```bash
OLLAMA_REMOTE_1=http://192.168.1.10:11434
OLLAMA_REMOTE_2=http://192.168.1.20:11434
```

Then regenerate the Olla config:
```bash
scripts/generate-olla-config.sh
docker compose restart olla
```

**[Multi-machine guide → docs/multi-machine.md](docs/multi-machine.md)**

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Install](docs/install.md) | Full setup walkthrough |
| [Getting started](docs/getting-started.md) | First steps after install |
| [Multi-machine](docs/multi-machine.md) | Connecting multiple nodes |
| [Smart router](docs/smart-router.md) | How model routing works |
| [Model guide](docs/model-guide.md) | Choosing and managing models |
| [Cloud models](docs/cloud-models.md) | Configuring Claude, Gemini, OpenAI |
| [Hardware](docs/getting-started.md) | GPU setup (Arc, NVIDIA, CPU) |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |

---

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines and [SECURITY.md](SECURITY.md) for responsible disclosure.

---

## License

MIT — use freely, build freely.
