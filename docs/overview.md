# What is ai-stack?

ai-stack is an open-source toolkit for running AI language models on your own computer — completely free, with no ongoing subscription, no per-token cost, and no data leaving your machine.

Once set up, you have a fully functional AI development assistant that can help with code, answer questions, search your notes, and more — without ever pinging a paid API.

---

## Why run AI locally?

Most AI tools you've used (ChatGPT, Claude.ai, Gemini) are cloud services. Every message you send goes to a company's server, gets processed, and comes back as a response. This means:

- **You pay per use** — directly (subscription) or indirectly (your data)
- **Your conversations are stored** — and may be used to train future models
- **You need internet** — no connectivity, no AI
- **You have a quota** — rate limits, tier restrictions, usage caps

Running locally eliminates all of this. The model runs on your hardware. Your data stays on your machine. There's no usage meter.

The trade-off: local models are smaller than frontier cloud models. GPT-4 or Claude Opus have vastly more parameters than what you can run locally. But for most development tasks — writing code, explaining concepts, answering questions about your own notes — a 14-billion parameter local model is genuinely useful and fast enough to be practical.

---

## What can I do with this?

Once the stack is running, you can:

- **Write and debug code** — ask questions, get explanations, have the AI write and fix scripts
- **Search your notes** — if you use Obsidian, the retriever service makes your entire vault searchable via natural language
- **Run multiple models** — route different types of tasks to different models automatically (reasoning tasks to DeepSeek, code to Qwen-Coder, etc.)
- **Use cloud models as a backup** — if a task genuinely needs a frontier model (like Claude Sonnet or Gemini Pro), you can route to those via free-tier APIs without changing your workflow
- **Span multiple machines** — if you have more than one computer on your network, Olla can load-balance requests across all of them

---

## How does it work?

The stack has four main services:

### 1. Ollama — the model runner

[Ollama](https://ollama.com) manages downloading, storing, and running language models on your hardware. It handles the complex GPU memory management and exposes a simple API. Think of it as the engine.

ai-stack uses a version of Ollama that supports Intel Arc GPUs (via Intel's OneAPI/SYCL framework). If you have Nvidia or AMD hardware, standard Ollama works the same way.

### 2. LiteLLM — the cloud gateway

[LiteLLM](https://litellm.ai) is a proxy that gives you one consistent API to talk to cloud models (Claude, Gemini, etc.). Free-tier access to Claude and Gemini works through here — you provide an API key, LiteLLM handles the translation.

This is entirely optional. If you don't add API keys, LiteLLM just sits idle.

### 3. Olla — the load balancer

[Olla](https://github.com/elestio/olla) sits in front of Ollama and LiteLLM and presents them as a single unified endpoint. If you add more machines running Ollama to your network, Olla discovers them and spreads requests across all of them.

You don't interact with Olla directly — OpenCode and the smart router use it automatically.

### 4. Smart Router — the model selector

The smart router (`router/smart_model_router.py`) looks at every request before it reaches Olla and picks the best model for the job. If you ask a coding question, it routes to the code model. If you ask a reasoning question, it routes to the reasoning model. If your request includes tool definitions (like searching your vault), it routes to a model that supports tool calling.

Classification uses a small dedicated model (qwen2.5:1.5b) and adds ~100–500ms per request — still fast enough to feel instant.

### 5. Retriever — the note searcher

The retriever service indexes your Obsidian vault and makes it searchable via natural language. It uses a combination of keyword search and vector similarity to find relevant notes. OpenCode can call it as a tool, so you can ask "what did I write about DNS?" and get actual results from your notes.

No internet required — embeddings run locally through Ollama.

### How requests flow

```
You type a message in OpenCode
        ↓
Smart Router (classifies your request, picks the best model)
        ↓
Olla (routes to the selected model, load-balances if multiple machines)
        ↓
Ollama (runs the model on your GPU) — or — LiteLLM (calls cloud API)
        ↓
Response comes back to you

If your request needs your notes:
Smart Router detects tool use → routes to tool-capable model
Model calls vault-search tool → Retriever searches your vault
Results returned to model → included in response
```

---

## What hardware do I need?

You need a reasonably modern computer with Linux. The more RAM you have, the larger models you can run. You do not need an expensive dedicated GPU — this stack is designed to work on integrated graphics and CPUs as well.

| RAM | What you can run |
|-----|----------------|
| 16 GB | 7B models (Mistral 7B, Llama 3.1 8B) — functional but limited |
| 32 GB | 14B models (Qwen 14B, DeepSeek-R1 14B) — solid for development work |
| 48 GB+ | 27B models (Gemma3 27B) — high-quality output, handles complex tasks |

If you have an Intel Arc iGPU (found in Intel Core Ultra processors), the stack has specific support for using it to accelerate inference. Nvidia and AMD GPUs work through standard Ollama. CPU-only also works — slower, but functional.

See [docs/hardware/](hardware/) for hardware-specific setup guides.

---

## Do I need internet access?

For the initial setup: yes. You need to download Docker images and model files (which can be several gigabytes each).

After setup: no. The entire stack runs offline. The only exception is if you configure cloud API keys (Claude, Gemini) — those calls go to the cloud, but they're optional.

---

## Where do I start?

→ **[docs/install.md](install.md)** — complete setup walkthrough, start here

After install:
→ **[docs/getting-started.md](getting-started.md)** — your first conversation with a local model
→ **[docs/models.md](models.md)** — which models to use for what
→ **[docs/smart-router.md](smart-router.md)** — how automatic model selection works
