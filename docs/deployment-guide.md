# Deployment Guide

A step-by-step guide for setting up the AI Stack on Linux with Intel Arc iGPU.

---

## Prerequisites

- **Linux** — tested on Fedora, Ubuntu, Arch
- **Intel Arc iGPU** — or other GPU supported by Ollama
- **Docker Engine 24+** and Docker Compose v2 plugin
- **32 GB RAM** recommended (iGPU shares system memory)
- **Git**

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/ai-stack.git
cd ai-stack
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

| Variable | What to set |
|----------|-------------|
| `STACK_USER` | Your Linux username |
| `LITELLM_MASTER_KEY` | Change from default — use `sk-local-` + random hex |
| `ANTHROPIC_API_KEY` | Your Anthropic API key (for Claude) |
| `GEMINI_API_KEY` | Your Google AI API key (for Gemini) |
| `WEBUI_SECRET_KEY` | Generate with: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

```bash
# Generate keys for all services
bash scripts/generate-keys.sh
```

### 3. Run the installer

```bash
bash install.sh
```

The installer automates:
- Creating required Docker volumes
- Installing the systemd service (`ai-stack.service`)
- Deploying pipeline files to the Pipelines container
- Starting the full stack
- Prompting you to pull models

### 4. Open WebUI

Open **http://localhost:3000** and create your admin account (first user becomes admin).

> **Important:** The first account registered is the admin. Register immediately on first visit.

### 5. Post-install configuration

After first login, follow the [post-install guide](post-install.md) to configure:
- Ollama connection (`http://ollama-arc:11434`)
- Pipelines connection (`http://pipelines:9099`)
- Open Terminal integration
- System Diagnostics tool
- Smart Model Router

---

## Architecture Overview

```
Open WebUI :3000
  ├── Ollama API → Olla :40114/olla/ollama → ollama-arc :11434 (Intel Arc iGPU)
  │                                       → remote Ollama nodes (LAN, optional)
  └── OpenAI API → LiteLLM :4000/v1 → Claude (Anthropic)
                                     → Gemini (Google)

Khoj :42110 → Olla :40114/olla/ollama/v1/ → ollama-arc (RAG over Obsidian vault)
```

All traffic flows through **Olla** (port 40114) as the unified LLM router. This means you only configure one endpoint in your tools.

### Service Quick Reference

| Service | Port | Purpose |
|---------|------|---------|
| Open WebUI | 3000 | Chat UI, admin panel |
| Olla | 40114 | LLM router / load balancer |
| LiteLLM | 4000 | Cloud model proxy (Claude, Gemini) |
| Ollama (arc) | 11434 | Local LLM runner (Intel Arc iGPU) |
| Pipelines | 9099 | Query routing, code execution pipeline |
| Open Terminal | 8000 | Terminal in the browser |
| Khoj | 42110 | AI search over your notes |
| Khoj DB | 5432 | Postgres for Khoj |

---

## Verifying the Stack

Once the stack is running, verify everything is healthy:

```bash
# Olla (LLM router)
curl http://localhost:40114/health

# Ollama (local models)
curl http://localhost:11434/api/tags

# LiteLLM (cloud gateway)
curl http://localhost:4000/health/liveness

# Open WebUI
curl http://localhost:3000/health
```

Check models are available:

```bash
# List installed models
docker exec ollama-arc ollama list

# Check what's loaded in GPU memory
curl http://localhost:11434/api/ps | python3 -m json.tool
```

Verify the GPU is working:

```bash
docker logs ollama-arc 2>&1 | grep -i "device\|gpu\|arc\|oneapi"
```

Expected output shows `oneapi` as the inference engine and VRAM > 0.

---

## Day 2 Operations

### Start / Stop / Restart (via systemd)

```bash
sudo systemctl start ai-stack.service
sudo systemctl stop ai-stack.service
sudo systemctl restart ai-stack.service
sudo systemctl status ai-stack.service
```

### Direct Docker Compose (for testing)

```bash
# Start with pre-flight checks
bash start.sh -d

# Or start directly (no pre-flight)
docker compose up -d

# Stop
docker compose down
```

### View logs

```bash
# All services
docker compose logs --tail=50 -f

# Single service
docker logs open-webui --tail=30 -f
docker logs ollama-arc --tail=30 -f
```

### Pull new models

```bash
docker exec ollama-arc ollama pull deepseek-r1:14b
docker exec ollama-arc ollama pull qwen2.5-coder:14b
docker exec ollama-arc ollama pull gemma3:12b
docker exec ollama-arc ollama pull nomic-embed-text:latest
```

### Add a remote Ollama node

1. Add to `.env`: `OLLAMA_REMOTE_MYNODE=http://192.168.1.50:11434`
2. Regenerate Olla config: `bash scripts/generate-olla-config.sh`
3. Restart: `sudo systemctl restart ai-stack.service`

### Update the stack

```bash
git pull
docker compose pull
sudo systemctl restart ai-stack.service
```

### Edit configuration and reload

If you change `.env` and need to apply:

```bash
# Regenerate Olla config (reads OLLAMA_REMOTE_* from .env)
bash scripts/generate-olla-config.sh

# Regenerate pipeline configs
bash install.sh  # (idempotent — safe to re-run)

# Restart the stack
sudo systemctl restart ai-stack.service
```

---

## Security Basics

- **Never commit `.env`** — it's gitignored, but double-check `git status` before committing
- **Change all default passwords** — `LITELLM_MASTER_KEY`, `WEBUI_SECRET_KEY`, `PIPELINES_API_KEY`, `KHOJ_ADMIN_PASSWORD` must not be defaults
- **Backup files** — If you create `.env.backup`, it's gitignored, but verify with `git status`
- **Network exposure** — All services bind to all interfaces by default. Put a reverse proxy with TLS in front for production

---

## Next Steps

| Guide | What it covers |
|-------|----------------|
| [post-install.md](post-install.md) | Open WebUI admin panel setup (connections, tools, pipelines) |
| [model-guide.md](model-guide.md) | Model recommendations for Intel Arc iGPU, Smart Router routing |
| [khoj-setup.md](khoj-setup.md) | Khoj / Obsidian vault integration |
| [troubleshooting.md](troubleshooting.md) | Common issues and how to fix them |
