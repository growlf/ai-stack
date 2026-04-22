# Model guide

Recommendations for Intel Arc iGPU with your available RAM.

---

## Recommended stack

| Model | Size | Use case |
|-------|------|----------|
| `qwen2.5:14b` | ~8.3 GB | Tool calling, health checks, diagnostics, general sysadmin |
| `qwen2.5-coder:14b` | ~8.3 GB | Scripts, configs, code, debugging |
| `deepseek-r1:14b` | ~8.3 GB | Complex reasoning, root cause analysis, architecture decisions |
| `gemma3:12b` | ~7.8 GB | Long log analysis, summaries, documentation |
| `nomic-embed-text` | ~274 MB | Embeddings for RAG / knowledge base |

---

## Why these models

**qwen2.5:14b** — Most reliable at tool calling in Open WebUI. The base variant is better at actually invoking tools vs writing code about them. The Smart Model Router defaults to this for sysadmin queries.

**qwen2.5-coder:14b** — Optimised for code and config work. Understands YAML, Dockerfiles, systemd units, bash. Better than the base model for anything involving file structure or shell commands.

**deepseek-r1:14b** — Thinks before responding (chain-of-thought). Worth the ~40 second overhead for complex problems. Not suitable for quick status checks.

**gemma3:12b** — Long context window, good at summarising large log files or documents. Less reliable at tool calling than qwen2.5.

**nomic-embed-text** — Lightweight embedding model needed if you use Open WebUI's knowledge base / RAG features with your documents.

---

## Memory considerations

Intel Arc iGPU shares system RAM. With sufficient RAM (32GB recommended):

- One 14b model loaded: ~8-9 GB GPU memory + ~4 GB system overhead = ~12-13 GB total
- OS + Docker overhead: ~4-6 GB
- Available for other processes: ~13-16 GB

Running two 14b models simultaneously will likely cause one to be paged out. Use `OLLAMA_KEEP_ALIVE=-1` (set in compose) to keep your primary model resident.

---

## Pulling models

```bash
# Pull the full recommended stack
docker exec ollama-arc ollama pull deepseek-r1:14b
docker exec ollama-arc ollama pull qwen2.5-coder:14b
docker exec ollama-arc ollama pull gemma3:12b
docker exec ollama-arc ollama pull qwen2.5:14b
docker exec ollama-arc ollama pull nomic-embed-text:latest

# Check what's installed
docker exec ollama-arc ollama list

# Check what's currently loaded in memory
curl http://localhost:11434/api/ps | python3 -m json.tool
```

---

## Removing unused models

If you migrated from another machine you may have models you don't need:

```bash
# List all models with sizes
docker exec ollama-arc ollama list

# Remove a model
docker exec ollama-arc ollama rm llama3:8b
```

Common candidates for removal if not in your workflow:
- `llama3:8b`, `llama3.1:8b`, `llama3.1:latest` — superseded by qwen2.5 for most tasks
- `mistral:7b` — older, less capable than qwen2.5:7b
- `qwen2.5:7b` — use 14b instead if RAM allows

---

## Smart Model Router routing table

The `smart_model_router` pipeline automatically routes queries to the best model:

| Trigger keywords | Model selected |
|-----------------|----------------|
| health, status, check, monitor, alert, ollama, docker, gpu, vram | `qwen2.5:14b` |
| script, bash, yaml, compose, dockerfile, code, error, install | `qwen2.5-coder:14b` |
| why, root cause, analyze, optimize, performance, architecture | `deepseek-r1:14b` |
| logs, summarize, document, report, explain this | `gemma3:12b` |
| (anything else) | `qwen2.5:14b` (default) |
