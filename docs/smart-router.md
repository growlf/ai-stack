# Smart Router

The smart router (`router/smart_model_router.py`) sits between OpenCode and Olla. Every request passes through it. It classifies the request and selects the most appropriate model before forwarding to Olla.

## What it does

1. Inspects the incoming request
2. Classifies the message content using a small LLM (`qwen2.5:1.5b`) to determine the task category
3. Routes to the model best suited for that task
4. If the request contains tool definitions and the task is `scripting`, routes to a tool-capable model
5. Verifies the selected model is actually loaded before routing — falls back gracefully if unavailable

## Routing logic

### Classification

The router sends the user's message to a dedicated classifier model (`qwen2.5:1.5b`) with a system prompt that categorizes into exactly one of:

| Category | Description | Routes to |
|----------|-------------|-----------|
| `scripting` | Code, bash, yaml, config, debugging, automation | `qwen2.5-coder:14b` |
| `reasoning` | Analysis, explanation, comparison, architecture, math | `deepseek-r1:14b` |
| `longform` | Summarization, document writing, editing, reports | `gemma3:12b` |
| `default` | General conversation, file questions, system queries, anything else | `qwen2.5:7b` |

The message is capped at 1000 characters for classification.

### Tool requests

If the request contains `tools` or `functions`, the router:
1. Classifies the message normally (to understand what the user wants)
2. If the task is `scripting`, replaces the selected model with the tool-capable model (`llama3.1:8b`)
3. If the selected model doesn't support tools, falls back to `qwen2.5:14b` (the largest tool-capable model available)

This prevents tool-call failures on reasoning models like `deepseek-r1` that don't support the tools API, while still using the classifier to understand the request context.

### Capability verification

Before routing, the router checks the capability registry to confirm:
- The selected model is currently loaded in Ollama
- The model supports the required features (tools, etc.)

If the model isn't available, the router falls back to any available model (using `best_available()`), with a final fallback to the `default` model.

## Capability registry

The registry is built at startup by querying Olla's `/v1/models` endpoint. It refreshes every 5 minutes in the background — never on the request path.

Tool support is inferred from model family:

| Supports tools | Does not support tools |
|---------------|----------------------|
| mistral, llama3.x, qwen2.5, qwen3, phi3.5, phi4, command-r, granite3 | deepseek-r1, gemma3, gemma4, nomic, mxbai, snowflake |

If Olla is unreachable at startup, the router falls back to a static model map and retries the registry on the next refresh cycle. In-flight requests are never blocked by registry refreshes.

## Debug endpoint

```bash
curl http://localhost:40115/v1/router/capabilities
```

Returns the current registry state: which models are loaded, which support tools, and what the router would select for a tools request. Useful for diagnosing routing decisions without reading logs.

## Health check

```bash
curl http://localhost:40115/health
```

Response includes `models_loaded` count and `registry_stale` flag.

## Latency profile

Classification uses a small dedicated model (`qwen2.5:1.5b`), not the main task model. Expected latency:

| Step | Cost |
|------|------|
| Tools/functions check | nanoseconds (dict key lookup) |
| LLM classification | ~100–500ms (qwen2.5:1.5b inference, kept resident) |
| Capability registry lookup | nanoseconds (in-memory dict) |
| JSON encode/decode | unavoidable proxy overhead |

**Total per-request overhead: ~100–500ms.** This is higher than the previous regex-based classifier, but the classification is more accurate and adaptive. The classifier model is kept resident via `OLLAMA_KEEP_ALIVE=-1` so there's no cold-start penalty for individual requests.

## Configuration

All settings via environment variables in `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLASSIFY_MODEL` | `qwen2.5:1.5b` | Model used for content classification |
| `CAPABILITY_REFRESH_INTERVAL` | `300` | Seconds between registry refreshes |
| `ROUTER_PORT` | `40115` | Port the router listens on |
| `ENABLE_AUTO_TOOLS` | `false` | Automatically inject available tools into requests (planned) |

To change the model assigned to a category or the classifier model, edit the `MODELS` and `_CLASSIFY_MODEL` values at the top of `router/smart_model_router.py`.

---

## Planned: automatic tool injection

The current router handles tools defensively — if a request includes tool definitions, it routes to a capable model; if the request has no tools, they're not added.

A planned follow-on feature (`ENABLE_AUTO_TOOLS`) will change this: when routing to a tool-capable model, the router will automatically inject available tool definitions (vault search, etc.) into the request even when the client didn't ask for them. This allows the model to use tools proactively — for example, searching your vault when answering a question about your notes without you needing to explicitly invoke the tool.

**Design decisions already made:**
- Tool schemas will be defined in `router/tools.json` (OpenAI function-calling format)
- Injection happens in the router's `handle_request()`, not at the LiteLLM layer
- The env var defaults to `false` until the feature is tested and stable
- The model decides whether to invoke tools — injection makes them available, it doesn't force their use
