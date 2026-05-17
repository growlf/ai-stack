# Model reference

## Recommended stack

| Model | Size (RAM) | Tools | Use case |
|-------|-----------|-------|----------|
| `mistral-small3.2:24b` | ~15 GB | yes | Function calling, 128K context, strong instruction following |
| `qwen3.5:27b` | ~17 GB | yes | General default — reasoning + tool calling |
| `qwen2.5:14b` | ~8.3 GB | yes | Diagnostics, sysadmin, structured output |
| `qwen2.5-coder:14b` | ~8.3 GB | yes | Code, scripts, configs, debugging |
| `deepseek-r1:14b` | ~8.3 GB | **no** | Complex reasoning, root cause analysis — no tool support |
| `gemma3:12b` | ~7.8 GB | **no** | Long log analysis, summaries, documentation |
| `llama3.1:8b` | ~4.9 GB | yes | Tool calling, fallback |
| `nomic-embed-text` | ~274 MB | — | Embeddings for RAG (retriever service) |

## Tool support

Models marked **no** in the Tools column do not support the OpenAI-compatible `tools`/`functions` API. Sending a tool-equipped request to these models returns an error from Ollama.

The smart router handles this automatically — requests with tool definitions are always routed to a tools-capable model regardless of content classification. You do not need to manually avoid these models when using OpenCode with tools configured.

If you query Ollama directly (bypassing the router), use only tools-capable models for tool-calling requests.

## Auto-selection (legacy reference)

> *The earlier `apostle.py catalog` command provided a RAM-budget recommendation for which models fit a given node. As of 2026-05-16 this functionality is superseded by Shepherd's hardware-probe + future palette-orchestrator design; `scripts/apostle.py` may still exist in the repo as legacy code but is not the current path. The RAM-budget logic below is preserved as design notes for the eventual palette-orchestrator (see [PLANS.md](../PLANS.md) and `~/enclave-core/docs/ai-stack-cross-model-tuning-brainstorm.md`).*

The original budget algorithm:

- **Budget**: 70% of total system RAM by default
- **Laptop cap**: max 8GB per model when `OLLAMA_PROFILE=laptop`
- **Ultra-light cap**: max 3GB per model when `OLLAMA_PROFILE=ultra-light`
- **Priority**: models tagged `priority=high` are recommended first, then sorted by role fit

Today: choose models for each node manually based on its hardware, pull them with `ollama pull`, and let Olla federation expose them across the herd. The Shepherd dashboard at `:40117` shows what's resident on each peer.

## Model selection notes

**`deepseek-r1:14b`** — Chain-of-thought reasoning. Thinks before responding, typically adding 20–60 seconds to response time. Worth it for complex root cause analysis or architectural decisions. Not suitable for quick queries or anything requiring tool calls.

**`qwen3.5:27b`** — Requires ~17 GB GPU memory. On systems with 32 GB RAM, loading this model alongside others will cause page-outs. Pull on demand rather than keeping resident. Use for tasks that need large context or complex analysis where smaller models underperform.

**`qwen2.5-coder:14b`** — Understands YAML, Dockerfiles, systemd units, and bash idioms better than the base `qwen2.5` model. Prefer this for anything involving file structure, shell commands, or configuration.

**`nomic-embed-text`** — Only used by the retriever service for vault embeddings. Not suitable for chat.

## Memory management

Ollama loads models into GPU memory on first use and keeps them resident for `OLLAMA_KEEP_ALIVE` duration (default: `-1`, meaning indefinitely). On systems with limited RAM, loading a second large model will evict the first.

```bash
# Check what's currently loaded
curl http://localhost:11434/api/ps | python3 -m json.tool

# Pull a model
docker exec ollama ollama pull qwen3.5:27b

# List all installed models
docker exec ollama ollama list

# Remove a model
docker exec ollama ollama rm llama3:8b
```

## Pulling the full recommended stack

```bash
docker exec ollama ollama pull mistral-small3.2:24b
docker exec ollama ollama pull qwen3.5:27b
docker exec ollama ollama pull qwen2.5-coder:14b
docker exec ollama ollama pull qwen2.5:14b
docker exec ollama ollama pull deepseek-r1:14b
docker exec ollama ollama pull gemma3:12b
docker exec ollama ollama pull llama3.1:8b
docker exec ollama ollama pull nomic-embed-text:latest

docker exec ollama ollama pull qwen3.5:27b  # optional — needs 48 GB+ RAM
```
