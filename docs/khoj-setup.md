# Khoj setup

Khoj is your AI second brain — it indexes your Obsidian vault and lets you ask questions
about your notes using your local Ollama models. No data leaves your machine.

Khoj runs at `http://localhost:42110` (or your configured `KHOJ_PORT`).

---

## Prerequisites

Before starting, make sure `nomic-embed-text` is pulled — Khoj uses it for embeddings:

```bash
docker exec ollama-arc ollama pull nomic-embed-text:latest
```

---

## First login

Navigate to `http://localhost:42110` and log in with the credentials from your `.env`:

- **Email:** `KHOJ_ADMIN_EMAIL`
- **Password:** `KHOJ_ADMIN_PASSWORD`

---

## Get your API key

1. Go to `http://localhost:42110/settings`
2. Under **API Keys**, create a new key
3. Copy it — you'll need it for the Obsidian plugin

---

## Obsidian plugin setup

1. Open Obsidian → **Settings → Community Plugins → Browse**
2. Search for **Khoj** and install it
3. Enable the plugin
4. Open the Khoj plugin settings and configure:

| Setting | Value |
|---------|-------|
| Server URL | `http://localhost:42110` |
| API Key | your key from the step above |

5. Click **Force Sync** to index your vault immediately

By default Khoj will auto-sync your vault periodically. Force Sync triggers it on demand.

---

## Using Khoj

**From Obsidian:**
- Click the Khoj chat icon 💬 in the ribbon
- Or run `Khoj: Chat` from the Command Palette
- Ask questions like "what did I write about DNS last week?" or "summarize my notes on Cascade STEAM"

**Find similar notes:**
- Run `Khoj: Find Similar Notes` from the Command Palette to see notes related to the one you're currently viewing

**From the browser:**
- Go to `http://localhost:42110` for the full Khoj web interface
- Create custom agents with specific knowledge bases from subsets of your vault

---

## Choosing a chat model

By default Khoj uses Ollama via `OPENAI_BASE_URL=http://ollama-arc:11434/v1/`. To set which
model Khoj uses for chat:

1. Go to `http://localhost:42110/settings`
2. Under **Chat Models**, add or select a model
3. Use any model name from your Ollama instance (e.g. `gemma3:12b`, `qwen2.5:14b`)

`gemma3:12b` is recommended for Khoj — its long context window handles large vault searches well.

---

## Vault path

Your Obsidian vault is mounted read-only into the Khoj container at `/vault`. The path on
your host is set by `OBSIDIAN_VAULT_PATH` in `.env`.

If you change the vault path, update `.env` and restart the stack:

```bash
sudo systemctl restart ai-stack.service
```

---

## Troubleshooting

**Khoj won't start:**
```bash
docker logs khoj --tail 30
docker logs khoj-db --tail 10
```
Most startup failures are database connection issues — ensure `khoj-db` is healthy first.

**Vault not indexing:**
- Confirm `OBSIDIAN_VAULT_PATH` in `.env` points to a real directory
- Check the vault is mounted: `docker exec khoj ls /vault`
- Trigger a manual sync from the Obsidian plugin settings

**nomic-embed-text errors:**
```bash
docker exec ollama-arc ollama list | grep nomic
# If missing:
docker exec ollama-arc ollama pull nomic-embed-text:latest
```

**Khoj can't reach Ollama:**
Both containers are on `ai-net` — verify:
```bash
docker exec khoj curl -s http://ollama-arc:11434/api/tags | python3 -m json.tool | head -5
```
