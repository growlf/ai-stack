# Cloud models (Claude, Gemini)

ai-stack is designed to work entirely locally with no cloud dependency. But free-tier access to frontier cloud models (Claude and Gemini) is available as an optional augment — useful when a task genuinely needs more capability than a 14B local model provides.

This is handled through LiteLLM, which proxies cloud API calls through a local endpoint. From OpenCode's perspective, it's just another provider.

---

## What "free tier" means

Both Anthropic (Claude) and Google (Gemini) offer free API access:

- **Claude** (via Anthropic): Generous free tier on Claude Haiku and Claude Sonnet. Rate-limited, but sufficient for occasional use. Requires account creation at [console.anthropic.com](https://console.anthropic.com).
- **Gemini** (via Google AI Studio): Free tier on Gemini Flash and Gemini Pro. More liberal rate limits. Requires Google account at [aistudio.google.com](https://aistudio.google.com).

Free tiers can be revoked or changed by the providers. Check current limits at their respective developer consoles.

---

## Getting API keys

### Claude (Anthropic)

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account (free)
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-...`)

### Gemini (Google AI Studio)

1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Sign in with your Google account
3. Click **Get API Key** → **Create API key in new project**
4. Copy the key

---

## Adding keys to the stack

### Via `.env` directly

Edit `.env` and set:

```bash
ANTHROPIC_API_KEY=sk-ant-your-key-here
GEMINI_API_KEY=your-gemini-key-here
```

Then restart:
```bash
sudo systemctl restart ai-stack.service
```

### Via Bitwarden/VaultWarden (recommended)

If you configured Bitwarden during install, use the placeholder format instead:

```bash
ANTHROPIC_API_KEY=<vaultwarden:your-org-id/anthropic-api-key>
GEMINI_API_KEY=<vaultwarden:your-org-id/gemini-api-key>
```

The stack resolves these at startup. See [docs/secret-management.md](secret-management.md) for details.

---

## Verifying cloud models are available

After adding keys and restarting:

```bash
# Check LiteLLM is healthy
curl http://localhost:4000/health/liveness

# List available models
curl http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY" | python3 -m json.tool
```

You should see Claude and Gemini models in the list.

---

## Using cloud models in OpenCode

Cloud models are available through the LiteLLM provider (`:4000`). In OpenCode, you can switch providers or configure cloud models as a fallback.

To direct a specific request to a cloud model, select the LiteLLM provider in OpenCode and choose the model explicitly. The smart router routes to local models by default — cloud models are not part of the automatic routing unless you configure them in the router's `MODELS` map.

**When to use cloud models:**
- Complex reasoning that requires a frontier-scale model
- Very long documents that exceed local model context limits
- Tasks where output quality matters more than privacy/cost

**When to stick with local:**
- Anything involving sensitive information
- Repetitive or bulk tasks (free tier has rate limits)
- When you need fast iteration (local is often faster for short tasks)

---

## The LiteLLM configuration

Cloud model definitions live in `proxy/litellm_config.yaml`. The default config includes:

```yaml
model_list:
  - model_name: claude-haiku
    litellm_params:
      model: anthropic/claude-haiku-20240307
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-20240620
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: gemini-flash
    litellm_params:
      model: gemini/gemini-1.5-flash
      api_key: os.environ/GEMINI_API_KEY

  - model_name: gemini-pro
    litellm_params:
      model: gemini/gemini-1.5-pro
      api_key: os.environ/GEMINI_API_KEY
```

To add more models or change which models are available, edit this file and restart the stack.

---

## Rate limits and costs

Free tiers have limits. If you hit them, LiteLLM will return a rate limit error (429). The stack does not automatically retry or fall back to another provider.

To avoid surprises:
- Use local models for routine tasks
- Reserve cloud calls for tasks where the quality difference matters
- Watch your usage at the provider consoles

If you start regularly hitting free tier limits and want to add paid credits, simply add credits to your Anthropic or Google AI account — no config changes needed.
