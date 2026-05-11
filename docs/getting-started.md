# Getting started

You've installed ai-stack and the services are running. Now what?

---

## Your first conversation

Open a terminal and type:

```bash
opencode
```

You'll see the OpenCode interface. Type a message and press Enter.

**Try something simple first:**
```
What's the difference between a process and a thread?
```

What happens:
1. Your message hits the smart router
2. The router classifies it (general question → default model)
3. The request goes to Olla, then to your local Ollama instance
4. The model generates a response on your hardware

The first response on a freshly started model takes 5–30 seconds while the model loads into GPU memory. Subsequent responses in the same session are faster.

---

## Understanding what you're talking to

By default, OpenCode routes through the smart router at `http://localhost:40115`. The router picks the best available local model for your request. You can check what model handled your last request — the router logs its decisions.

To see what models are available:
```bash
docker exec ollama ollama list
```

To see what's currently loaded in memory:
```bash
curl http://localhost:11434/api/ps | python3 -m json.tool
```

---

## The smart router in action

The router classifies your messages automatically. You don't need to think about model selection.

**Try these to see routing in action:**

For a reasoning question (routes to DeepSeek-R1):
```
Why does a TCP connection require a three-way handshake? Walk me through the reasoning.
```

For a code question (routes to Qwen-Coder):
```
Write a bash script that finds all files modified in the last 24 hours and prints their sizes.
```

For a diagnostic question (routes to Qwen 2.5):
```
My systemd service keeps restarting. What's the first thing I should check?
```

For a long-form task (routes to Gemma3):
```
Summarize the key differences between microservices and monolithic architectures for a presentation.
```

See [docs/smart-router.md](smart-router.md) for the full routing logic.

---

## Searching your notes (Obsidian vault RAG)

If you set `RETRIEVER_VAULT_PATH` in `.env` and your vault has been indexed, you can ask questions about your own notes directly in OpenCode.

The vault-search tool is automatically available in OpenCode when running from the project directory. When you ask a question that might be answered from your notes, the model calls the tool automatically.

**Try:**
```
What have I written about Docker networking?
```

Or from inside the ai-stack directory, the vault search is always available as a tool. The model will search your vault and incorporate the results into its answer.

Check indexing status:
```bash
curl http://localhost:42000/health
```

Look for `indexed_files` to confirm your vault is indexed. If it's 0, the vault may still be indexing (give it a few minutes for a large vault) or the path may be wrong.

---

## Using cloud models

If you added API keys for Claude or Gemini during setup, those models are available through LiteLLM. You can switch to a cloud model when you need more capability than local models provide.

In OpenCode, switch the provider to `litellm` to access cloud models directly. See [docs/cloud-models.md](cloud-models.md) for configuration details.

---

## Useful commands

```bash
# Check all services are healthy
curl http://localhost:40114/internal/health   # Olla
curl http://localhost:11434/api/tags          # Ollama
curl http://localhost:42000/health            # Retriever
curl http://localhost:40115/health            # Smart router

# See the smart router's current model routing decisions
curl http://localhost:40115/v1/router/capabilities

# Pull a new model
docker exec ollama ollama pull qwen2.5-coder:14b

# Watch logs from all services
docker compose logs --tail=20 -f

# Watch only the smart router (to see routing decisions)
docker logs router --tail=20 -f

# Restart everything
sudo systemctl restart ai-stack.service
```

---

## Adding more models

The more models you have, the better the router can match tasks to the right tool. See [docs/models.md](models.md) for the recommended stack and what each model does well.

To pull the full recommended model set:
```bash
docker exec ollama ollama pull mistral-small3.2:24b   # tool calling
docker exec ollama ollama pull qwen3.5:14b             # general default
docker exec ollama ollama pull qwen2.5-coder:14b       # code
docker exec ollama ollama pull qwen2.5:14b             # diagnostics
docker exec ollama ollama pull deepseek-r1:14b         # reasoning
docker exec ollama ollama pull gemma3:12b              # long-form
docker exec ollama ollama pull nomic-embed-text:latest # embeddings
```

Each model is 7–15 GB. Pull only what you have storage for.

---

## Something isn't working?

→ [docs/troubleshooting.md](troubleshooting.md) — solutions to common issues
