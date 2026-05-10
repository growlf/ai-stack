# Model guide

Recommendations for Intel Arc iGPU with your available RAM.

---

## Recommended stack

| Model | Size | Use case |
|-------|------|----------|
| `gemma4:27b` | ~16 GB | Heavy lifting, large context, complex analysis |
| `mistral-small3.2:24b` | ~15 GB | Strong function calling, instruction following, 128K context |
| `qwen3.5:14b` | ~8.5 GB | Latest Qwen, improved reasoning + tool calling (recommended default) |
| `qwen2.5:14b` | ~8.3 GB | Tool calling, health checks, diagnostics, general sysadmin |
| `qwen2.5-coder:14b` | ~8.3 GB | Scripts, configs, code, debugging |
| `deepseek-r1:14b` | ~8.3 GB | Complex reasoning, root cause analysis, architecture decisions |
| `gemma3:12b` | ~7.8 GB | Long log analysis, summaries, documentation |
| `nomic-embed-text` | ~274 MB | Embeddings for RAG / knowledge base |

---

## Why these models

**gemma4:27b** — Google's latest, strong at long-context reasoning and complex analysis. Requires ~16 GB GPU memory — best on systems with 48 GB+ RAM. Load on demand rather than keeping resident.

**mistral-small3.2:24b** — Updated Mistral Small with improved function calling, instruction following, and fewer repetition errors. 128K context window. Good middle ground between 14b and 27b models.

**qwen3.5:14b** — The latest Qwen generation with improved reasoning and tool calling. Recommended as the default model if your RAM allows alongside the rest of the stack.

**qwen2.5:14b** — Still solid at tool calling. Falls back to this if qwen3.5 isn't available or you need the smaller footprint.

**qwen2.5-coder:14b** — Optimised for code and config work. Understands YAML, Dockerfiles, systemd units, bash. Better than the base model for anything involving file structure or shell commands.

**deepseek-r1:14b** — Thinks before responding (chain-of-thought). Worth the ~40 second overhead for complex problems. Not suitable for quick status checks.

**gemma3:12b** — Long context window, good at summarising large log files or documents. Less reliable at tool calling than qwen2.5.

**nomic-embed-text** — Lightweight embedding model used by the retriever service for vault RAG.

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
docker exec ollama-arc ollama pull gemma4:27b
docker exec ollama-arc ollama pull mistral-small3.2:24b
docker exec ollama-arc ollama pull qwen3.5:14b
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

## Model routing

OpenCode handles model selection per conversation — choose the right model for each task. For automatic routing, configure multiple providers in your OpenCode config or use the Olla load balancer for priority-based routing.
