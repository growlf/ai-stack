# Shepherd — Herd Observability for ai-stack

> The name follows the project's existing herd metaphor (Ollama = llamas, federation, palette, warm pool). A shepherd watches over a herd — counts the flock, notices when one wanders, alerts when something looks wrong.

## Deployment on herd peers (auto-pull pattern)

**Operating model:** cluster-llm is the canonical edit/test node. All other herd peers (lab1-4, nuk1, Phoenix) pull main and auto-redeploy on a daily cron. No per-node operator SSH after initial setup.

**One-time setup on a new BMS peer:**

```bash
git clone git@github.com:growlf/ai-stack.git ~/ai-stack    # or https:// if no key
~/ai-stack/scripts/shepherd-auto-deploy.sh node            # first run, verifies works
crontab -e
# Append (4:17am daily, off-peak, non-:00):
# 17 4 * * * /home/<user>/ai-stack/scripts/shepherd-auto-deploy.sh node >> /tmp/shepherd-auto-deploy.log 2>&1
```

After that, daily 4:17am cron pulls main + redeploys if anything changed. See [`scripts/shepherd-auto-deploy.sh`](../scripts/shepherd-auto-deploy.sh) for env vars + role options (`node` / `control` / `both`).

**Prerequisites on each peer:** `python3-venv`, `git`, `curl`, SSH access to GitHub (deploy key or HTTPS). Lab nodes only need `node` role; cluster-llm runs `both`.

## What it does

Shepherd is the observability layer for the ai-stack herd. It replaces Apostle with a smaller, honest-by-construction service that watches every node and shows you the herd state at a glance.

## Architecture (v0.1.0 — initial scaffold)

```
                    ┌────────────────────────────┐
                    │ Shepherd Control-Plane     │
                    │ - Polls peers /herd/metrics│
                    │ - SSE-streams to browser   │
                    │ - Cross-source verify      │
                    └───────────┬────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼ pull every 5s    ▼                ▼
       ┌────────────┐     ┌────────────┐    ┌────────────┐
       │ Shepherd   │     │ Shepherd   │    │ Shepherd   │
       │ Node       │     │ Node       │    │ Node       │
       │ /herd/...  │     │ /herd/...  │    │ /herd/...  │
       └─────┬──────┘     └─────┬──────┘    └─────┬──────┘
             │                  │                 │
             ▼                  ▼                 ▼
      Probes + Ollama + Olla   ...                ...
```

## Quickstart

```bash
# Install
pip install -r requirements.txt

# Run a node sidecar (in cluster-llm or Phoenix's ai-stack docker-compose)
python -m shepherd_node                  # default :40116

# Run the control-plane (one designated node, usually cluster-llm)
python -m shepherd_control               # default :40117

# Open the dashboard
xdg-open http://localhost:40117/
```

## Endpoints (shepherd-node)

| Endpoint | What it returns |
|---|---|
| `GET /herd/metrics` | Current-state snapshot (system + hardware + Ollama + Olla) |
| `GET /herd/verify` | Raw secondary-source data for cross-source divergence detection |
| `GET /herd/schema` | JSON Schema of the metrics document (versioned) |
| `GET /herd/capabilities` | Orchestrator-facing summary of resident models + VRAM headroom |
| `GET /herd/history` | SQLite-backed historical query (v0.2) |
| `POST /herd/events/route` | Per-prompt routing event capture from Router (v0.2 storage) |
| `GET /herd/healthz` | Liveness check |

## Hardware probe support

| Hardware | Probe status | Notes |
|---|---|---|
| NVIDIA | ✅ implemented | Uses `nvidia-smi` subprocess |
| CPU-only | ✅ implemented | Floor probe — always available |
| Intel Arc | 🟡 stub | Detects hardware; metrics pending kernel/ipex-llm fix (see `phoenix-arc-investigation-backlog.md`) |
| Intel Iris / UHD | 🟡 stub | Detects hardware; metrics pending |
| AMD ROCm | 🟡 stub | Waits for first AMD-hardware contributor |
| Apple Silicon | 🟡 stub | Waits for first Mac contributor |
| Browser WebGPU | 🟡 stub | Browser-side participant; future browser-reports endpoint |

Adding a new hardware probe: drop a new file in `shepherd_node/probes/`, implement the `Probe` ABC, register it in `probes/__init__.py:discover_probes()`.

## Design doc

Canonical design at `~/enclave-core/docs/herd-shepherd-design.md`. Includes the full team-input synthesis, decision path on language (Python over Go for contribution-velocity), data shapes, dashboard sketch, and v2 deferrals.

## Status

- **v0.1.0** (this commit): scaffolding — endpoint surface, probe interface, system+Ollama+Olla collectors, NVIDIA + CPU probes implemented, stubs for all other hardware families. SQLite history + per-prompt event capture + control-plane SSE + UI all stubbed for v0.2.
- **v0.2** (next): SQLite ring-buffer history + Router patch for event capture + control-plane polling/aggregation + minimal dashboard.
- **v0.3** (then): D3 force-graph dashboard + Timeline tab + Verify tab + cross-source divergence detection.

## Contributing

Python 3.11+; FastAPI; pydantic v2. PRs welcome — particularly hardware probe implementations for non-NVIDIA hardware. The probe ABC is the extension surface; one file per hardware family.

— *Authored by Vela, synthesizing input from North, Forge, Scribe, Loom, Prove, Threshold, Keep (#ai-stack Project, 2026-05-15).*
