# Smart Router

The smart router (`router/smart_model_router.py`) sits between OpenCode and Olla. Every request passes through it. It classifies the request and selects the most appropriate model before forwarding to Olla.

## What it does

1. Inspects the incoming request
2. If the request contains tool definitions, routes to the designated tools-capable model
3. Otherwise, classifies the message content using keyword patterns and routes to the model best suited for that task
4. Verifies the selected model is actually loaded and capable before routing
5. Falls back gracefully if the preferred model is unavailable

## Routing logic

### Tool requests (highest priority)

If the request body contains a `tools` or `functions` field, the router bypasses content classification entirely and routes directly to the tools model (`mistral-small3.2:24b` by default). This prevents tool-call failures on reasoning models like `deepseek-r1` that don't support the tools API.

### Content classification

For requests without tools, the router scores the message against keyword patterns:

| Category | Patterns match | Routes to |
|----------|---------------|-----------|
| `tools` | tool, function, schema, json, api call | `mistral-small3.2:24b` |
| `reasoning` | why, analyze, explain, root cause, decision | `deepseek-r1:14b` |
| `code` | script, bash, python, docker, yaml, config | `qwen2.5-coder:14b` |
| `diagnostic` | log, error, service, process, permission | `qwen2.5:14b` |
| `longform` | summarize, document, report, long | `gemma3:12b` |
| `default` | (no strong match) | `qwen3.5:14b` |

The message is capped at 500 characters for classification — long documents are classified on their opening content.

### Capability verification

Before routing, the router checks the capability registry to confirm:
- The selected model is currently loaded in Ollama
- The model supports the required features (tools, etc.)

If the model isn't available or capable, the router falls back to the `default` model.

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

The router adds negligible latency to each request:

| Step | Cost |
|------|------|
| Tools/functions check | nanoseconds (dict key lookup) |
| Content classification | sub-millisecond (compiled regex on ≤500 chars) |
| Capability registry lookup | nanoseconds (in-memory dict) |
| JSON encode/decode | unavoidable proxy overhead |

**Total per-request overhead: well under 1ms.** The capability registry refresh happens in the background every 5 minutes — never on the hot path.

The startup registry load (one Olla API call) adds ~100ms to first startup. After that, routing decisions are pure in-memory.

## Configuration

All settings via environment variables in `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CAPABILITY_REFRESH_INTERVAL` | `300` | Seconds between registry refreshes |
| `ROUTER_PORT` | `40115` | Port the router listens on |
| `ENABLE_AUTO_TOOLS` | `false` | Automatically inject available tools into requests going to tool-capable models (planned feature — see below) |

To change the model assigned to a category, edit the `MODELS` dict at the top of `router/smart_model_router.py`.

---

## Planned: automatic tool injection

The current router handles tools defensively — if a request includes tool definitions, it routes to a capable model; if the request has no tools, they're not added.

A planned follow-on feature (`ENABLE_AUTO_TOOLS`) will change this: when routing to a tool-capable model, the router will automatically inject available tool definitions (vault search, etc.) into the request even when the client didn't ask for them. This allows the model to use tools proactively — for example, searching your vault when answering a question about your notes without you needing to explicitly invoke the tool.

**Design decisions already made:**
- Tool schemas will be defined in `router/tools.json` (OpenAI function-calling format)
- Injection happens in the router's `handle_request()`, not at the LiteLLM layer
- The env var defaults to `false` until the feature is tested and stable
- The model decides whether to invoke tools — injection makes them available, it doesn't force their use

This feature is tracked as a GitHub issue. It is not in the current release.
