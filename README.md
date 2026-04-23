# ai-stack

A self-hosted AI stack optimised for **Intel Arc iGPU** on Linux, built around Ollama + Open WebUI with automated model routing, system diagnostics tools, and a systemd-managed Docker Compose stack.

Built and documented through real-world homelab experience on Intel Arc hardware.

---

## What's included

| Component | Purpose |
|-----------|---------|
| **Ollama (ava-agentone/ollama-intel)** | LLM inference with Intel Arc iGPU acceleration via OneAPI/SYCL |
| **Open WebUI** | Chat interface with tool calling, pipelines, and terminal access |
| **Pipelines** | Server-side plugin system for model routing and workflow automation |
| **Open Terminal** | Browser-based terminal inside Open WebUI (with sudo support) |
| **Smart Model Router** | Auto-routes queries to the best model based on content |
| **System Diagnostics** | Tool for querying Ollama health, models, and VRAM across multiple machines |

---

## Hardware requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | Intel Core Ultra (Meteor Lake) | Intel Core Ultra 9 185H |
| RAM | 16 GB | 32 GB |
| GPU | Intel Arc iGPU | Intel Arc iGPU (any Meteor/Arrow Lake) |
| Storage | 50 GB free | 100 GB+ free (models are large) |
| OS | Ubuntu 22.04 | Ubuntu 24.04 |

> **Note:** This stack uses `ghcr.io/ava-agentone/ollama-intel` which replaced the archived `intelanalytics/ipex-llm-inference-cpp-xpu` image (archived January 28, 2026).

> **Note:** This stack has been specifically developed and tested on an Asus Zenbook Duo with an Intel Arc iGPU (Meteor Lake) running Ubuntu 24.04LTS. Other Intel scenarios should work, but have not been specifically tested - yet.  Please feel free to offer some patches or help us to add support for your system & environment.
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

# 4. Open
# http://localhost:3000
```

Then follow **[docs/post-install.md](docs/post-install.md)** for the Open WebUI configuration steps.

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
│   └── check-arc-gpu.sh        # GPU pre-flight (detects card0/card1 drift)
├── pipelines/
│   └── smart_model_router.py   # Auto-routes queries to best model
├── tools/
│   └── system_diagnostics.py   # Multi-instance Ollama health + model queries
└── docs/
    ├── post-install.md          # Open WebUI configuration checklist
    ├── model-guide.md           # Model recommendations and routing table
    └── troubleshooting.md       # Common issues and fixes
```

---

## Model stack

| Model | Use case |
|-------|----------|
| `qwen2.5:14b` | Tool calling, diagnostics, sysadmin (default) |
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

The System Diagnostics tool supports querying multiple Ollama instances across your LAN. Edit `OLLAMA_INSTANCES` in `tools/system_diagnostics.py`:

```python
OLLAMA_INSTANCES = {
    "local":   "http://ollama-arc:11434",   # this machine
    "remote1": "http://10.0.0.X:11434",     # remote machine on your LAN
}
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

## Related projects

- [ava-agentone/ollama-intel](https://github.com/Ava-AgentOne/ollama-intel) — Intel Arc optimised Ollama image
- [open-webui/open-webui](https://github.com/open-webui/open-webui) — Web interface
- [open-webui/pipelines](https://github.com/open-webui/pipelines) — Pipeline plugin system

---

## Licence

MIT — use freely, contributions welcome.

Built with ☕ and stubbornness.
