# Frequently asked questions

---

## General

### Is this really free to run?

Yes, once you have the hardware. There are no subscription fees, no per-token costs for local models, and no usage quotas. You're running models on your own hardware using your own electricity.

Cloud models (Claude, Gemini) via free-tier API keys are also free up to their rate limits. If you never configure cloud API keys, you'll never spend a cent.

### Do I need a powerful GPU?

No. You need a reasonably modern computer with enough RAM. Here's the practical breakdown:

- **No dedicated GPU, 16–32 GB RAM**: CPU inference works. Slow (30–120 seconds per response for a 14B model), but functional.
- **Integrated GPU (Intel Arc iGPU), 16–32 GB RAM**: GPU-accelerated inference. 3–10 seconds per response for 14B models. This is the primary target of this stack.
- **Dedicated GPU (Nvidia/AMD), any VRAM**: Fast. 1–5 seconds per response depending on VRAM.

For development work where you're not waiting on responses constantly, even CPU inference is useful.

### Will my data leave my machine?

Only if you explicitly configure cloud API keys (Anthropic, Google) and choose to use cloud models. All local model inference stays completely on your machine. The retriever service, smart router, and all local model calls are fully offline.

### Can I run this on Windows or macOS?

The stack is designed and tested on Linux. macOS may work for development purposes using Docker Desktop, but it's not officially supported and GPU acceleration won't work (Docker can't pass through GPU on macOS the same way).

Windows is similarly untested. If you're on Windows, consider using WSL2 with Ubuntu.

---

## Models

### What's a "14B model"? What do the numbers mean?

The number refers to parameters — roughly, how many numerical values the model has learned during training. More parameters generally means more capable but also more memory-hungry.

- **7B–8B**: Fast, small footprint. Good for simple tasks. Fits in ~5 GB.
- **14B**: The sweet spot for most tasks on 32 GB RAM. ~8–9 GB footprint.
- **27B**: High-quality output. Needs ~16 GB GPU memory. Requires 48 GB+ total RAM to run alongside other models.

Bigger isn't always better for every task — a 7B model fine-tuned for code can outperform a 14B general model on coding questions.

### Why does the first response take so long?

The model file (~8–15 GB) has to be loaded from disk into GPU memory. This happens on first use after startup or after the model has been evicted from memory (due to inactivity or loading another model). Subsequent responses in the same session are fast because the model stays loaded.

You can force a model to stay loaded permanently with `OLLAMA_KEEP_ALIVE=-1` in `.env`.

### Why can't I use tools with deepseek-r1?

DeepSeek-R1 doesn't implement the OpenAI tools/function-calling API. This is a limitation of the model, not the stack. The smart router detects when a request includes tool definitions and routes it to a tools-capable model (Mistral, Qwen, etc.) instead.

If you're calling the API directly (bypassing the smart router), you'll need to avoid sending `tools` or `functions` fields to deepseek-r1.

### How do I know which model handled my request?

The smart router logs every routing decision. Watch the router logs:
```bash
docker logs router --tail=20 -f
```

You can also query the router's capabilities endpoint to see what it would choose:
```bash
curl http://localhost:40115/v1/router/capabilities
```

### Can I add my own custom models?

Yes. Pull any model that Ollama supports:
```bash
docker exec ollama ollama pull <model-name>
```

To add it to the smart router's automatic routing, edit the `MODELS` dict in `router/smart_model_router.py` and assign it to a category. See [docs/smart-router.md](smart-router.md).

To add a fine-tuned or custom GGUF model, place the `.gguf` file in your Ollama models directory and create a Modelfile.

---

## Installation and setup

### The installer failed partway through. How do I retry?

The installer is designed to be re-runnable. Most steps are idempotent (safe to run multiple times). Just run `./install.sh` again.

If a specific step is failing, check the error message and consult [docs/troubleshooting.md](troubleshooting.md).

### I don't use Obsidian. Can I still use the stack?

Absolutely. The retriever service is entirely optional. Skip the `RETRIEVER_VAULT_PATH` configuration and the retriever will start but remain idle. You can set `RETRIEVER_VAULT_PATH` to any folder of Markdown files — not just an Obsidian vault.

The rest of the stack (local LLM inference, smart routing, cloud model access) works independently.

### Can I use this without OpenCode?

Yes. The stack exposes standard OpenAI-compatible APIs:
- Smart router: `http://localhost:40115/v1/chat/completions`
- Olla (direct): `http://localhost:40114/v1/chat/completions`
- LiteLLM: `http://localhost:4000/v1/chat/completions`

Any tool that can connect to an OpenAI-compatible endpoint works — [Continue.dev](https://continue.dev) for VS Code, [Aider](https://aider.chat), [LM Studio](https://lmstudio.ai), or curl.

Example with curl:
```bash
curl http://localhost:40115/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Write a hello world in Go"}]
  }'
```

### How do I update the stack?

```bash
git pull
docker compose pull
sudo systemctl restart ai-stack.service
```

---

## Networking and performance

### Responses are slow. What can I do?

1. **Check if the model is loaded**: `curl http://localhost:11434/api/ps`. If empty, the model needs to load on first request.
2. **Check GPU is being used**: `docker logs ollama 2>&1 | grep -i "device\|gpu\|oneapi"`. If you see CPU-only, the GPU isn't being used.
3. **Check memory pressure**: If system RAM is near full, the OS may be swapping, which kills performance.
4. **Consider a smaller model**: A 7B model running on GPU is faster than a 14B model on CPU.

### Can I run this on a headless server?

Yes. The stack has no GUI dependencies. Everything is CLI and API-based. Run it on a headless server, access via SSH, and point OpenCode at the server's IP.

### Does the smart router add latency?

Negligibly — under 1ms per request. See [docs/smart-router.md](smart-router.md) for the full latency profile.

### Can multiple people use this at once?

Yes, with limits. Ollama queues requests — if two people send requests simultaneously, one waits while the other is processed. For a small team (2–4 people with light usage), a single local Ollama instance is usually fine.

For heavier concurrent use, add more machines via Olla's multi-node routing — see [docs/multi-machine.md](multi-machine.md).

---

## Contributing

### How do I contribute?

See [CONTRIBUTING.md](../CONTRIBUTING.md). Bug reports, documentation improvements, and hardware compatibility reports are all welcome.

### I have a different GPU. Will this work?

Intel Arc iGPU is the primary tested hardware. Nvidia works through standard Ollama (no special configuration). AMD ROCm support through Ollama is improving — check the [Ollama documentation](https://ollama.com) for current status.

CPU-only works but is slow for 14B+ models.

Hardware-specific guides are in [docs/hardware/](hardware/).
