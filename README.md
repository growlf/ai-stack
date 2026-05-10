# ai-stack

A self-hosted AI stack optimised for **Intel Arc iGPU** on Linux, built around Ollama + OpenCode. Provides local LLM inference (ollama-arc), cloud API routing (LiteLLM), unified routing (Olla), and Obsidian vault RAG (retriever). The primary AI interface is **OpenCode** (CLI + Obsidian sidebar plugin).

Built and documented through real-world homelab experience on Intel Arc hardware.

---

## What's included

| Component | Purpose |
|-----------|---------|
| **Ollama (ava-agentone/ollama-intel)** | LLM inference with Intel Arc iGPU acceleration via OneAPI/SYCL |
| **LiteLLM** | Cloud API gateway (Claude, Gemini) |
| **Olla** | Unified LLM router with load balancing |
| **Retriever** | Lightweight Obsidian vault RAG (sqlite-vec + FTS5, hybrid search) |
| **OpenCode** | Primary AI interface — CLI tool + Obsidian sidebar plugin |

---

## Hardware requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | Intel Core Ultra (Meteor Lake) | Intel Core Ultra 9 185H |
| RAM | 16 GB | 32 GB |
| GPU | Intel Arc iGPU | Intel Arc iGPU (any Meteor/Arrow Lake) |
| Storage | 50 GB free | 100 GB+ free (models are large) |
| OS | Ubuntu 22.04 | Ubuntu 24.04 |

---

## Quick start

```bash
# 1. Clone the repo
git clone https://github.com/growlf/ai-stack.git
cd ai-stack

# 2. Configure
cp .env.example .env
nano .env   # set your username, paths, and API keys

# 3. Install
chmod +x install.sh scripts/check-arc-gpu.sh
./install.sh

# 4. OpenCode is the primary interface
opencode --provider olla --base-url http://localhost:40114

# 5. Check retriever health
curl localhost:42000/health
```

---

## Project structure

```
ai-stack/
├── install.sh                  # Main installer
├── docker-compose.yml          # Full stack definition
├── .env.example                # All configurable values
├── systemd/
│   └── ai-stack.service        # Systemd unit (auto-start on boot)
├── scripts/
│   ├── check-arc-gpu.sh        # GPU pre-flight (detects card0/card1 drift)
│   ├── discover-herd.sh        # mDNS discovery of remote Ollama nodes
│   └── generate-olla-config.sh # Reads .env → writes proxy/olla.yaml
├── retriever/
│   ├── main.py                 # FastAPI app
│   ├── search.py               # Hybrid search (FTS5 + vector, RRF fusion)
│   ├── indexer.py              # Vault scanner + watchdog + chunking
│   └── Dockerfile
├── proxy/
│   └── litellm_config.yaml     # LiteLLM model registry (Claude, Gemini)
└── docs/
    ├── deployment-guide.md     # Setup walkthrough
    ├── model-guide.md          # Model recommendations and routing
    ├── troubleshooting.md      # Common issues and fixes
    └── retriever-guide.md      # Obsidian vault RAG setup
```

---

## Model stack

| Model | Use case |
|-------|----------|
| `gemma4:27b` | Heavy lifting, large context, complex analysis |
| `mistral-small3.2:24b` | Strong function calling, 128K context |
| `qwen3.5:14b` | Improved reasoning, tool calling (recommended default) |
| `qwen2.5:14b` | Tool calling, diagnostics, sysadmin |
| `qwen2.5-coder:14b` | Scripts, configs, code |
| `deepseek-r1:14b` | Complex reasoning, root cause analysis |
| `gemma3:12b` | Log analysis, summaries, documentation |
| `nomic-embed-text` | Embeddings / RAG |

See **[docs/model-guide.md](docs/model-guide.md)** for details.

---

## Known Intel Arc quirks

- The DRI card node (`/dev/dri/card0` vs `card1`) can drift between reboots on Meteor Lake. The `check-arc-gpu.sh` pre-flight script detects and corrects this automatically.
- Intel iGPU uses shared system RAM — `runner.vram="0 B"` in Ollama logs is expected and normal.
- Use `OLLAMA_KEEP_ALIVE=-1` to keep models resident in memory between requests.
- `renderD128` is the compute node and is stable; only the `cardN` display node drifts.

---

## Multi-machine setup

Add remote Ollama nodes via `.env`:

```
OLLAMA_REMOTE_WORKSTATION=http://192.168.1.50:11434:75
```

Then regenerate Olla config:

```bash
bash scripts/generate-olla-config.sh
sudo systemctl restart ai-stack.service
```

Or auto-discover nodes on your LAN:

```bash
bash scripts/discover-herd.sh --apply
```

---

## Updating the stack

```bash
cd /path/to/ai-stack

# Pull latest images
docker compose pull

# Restart with new images
sudo systemctl restart ai-stack.service
```

---

## Licence

MIT — use freely, contributions welcome.

Built with ☕ and stubbornness.
