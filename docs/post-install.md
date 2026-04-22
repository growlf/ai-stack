# Post-install configuration guide

The installer handles everything that can be automated. These steps must be completed manually in the Open WebUI admin panel.

Open WebUI: http://localhost:3000 (or your configured WEBUI_PORT)

---

## 1. First login

On first visit, create your admin account. The first account registered becomes the admin.

---

## 2. Connections

Go to **Admin Panel → Settings → Connections**

**Ollama API**
- URL: `http://ollama-arc:11434`
- Toggle: enabled

**Pipelines** (add via the + button next to OpenAI API)
- URL: `http://pipelines:9099`
- API Key: value from your `.env` PIPELINES_API_KEY

---

## 3. Open Terminal

Go to **Admin Panel → Settings → Integrations**

- Enable the Open Terminal toggle
- URL: `http://open-terminal:8000`
- Auth: Bearer → value from your `.env` OPEN_TERMINAL_API_KEY

---

## 4. Install System Diagnostics tool

Go to **Admin Panel → Tools → + (Create Tool)**

Paste the full contents of `tools/system_diagnostics.py` from this repo into the editor and save.

Edit `OLLAMA_INSTANCES` near the top of the tool to match your setup:
```python
OLLAMA_INSTANCES = {
    "local": "http://ollama-arc:11434",      # always present
    # "remote1": "http://10.0.0.X:11434",   # add remote machines here
}
```

> **Why manual?** Tools are stored in Open WebUI's database, not on the filesystem.
> The installer can only deploy pipeline files — tools must be pasted through the UI.

---

## 5. Enable tools on models

Go to **Admin Panel → Models** → edit each model → under **Tools** check:
- System Diagnostics
- File Scout (if installed)

Do this for: `deepseek-r1:14b`, `qwen2.5-coder:14b`, `gemma3:12b`, `qwen2.5:14b`

---

## 5. Verify pipelines

Go to **Admin Panel → Settings → Pipelines**

You should see `http://pipelines:9099` listed and the following modules loaded:
- `events_pipeline`
- `python_code_pipeline`
- `smart_model_router`

If `smart_model_router` is missing, run:
```bash
docker exec pipelines rm -rf /app/pipelines/__pycache__
docker cp pipelines/smart_model_router.py pipelines:/app/pipelines/smart_model_router.py
docker restart pipelines
```

---

## 6. Create a sysadmin model (optional but recommended)

Go to **Admin Panel → Models → Manage → Create a model**

- Name: `Sysadmin`
- JSON: `{"model": "my-sysadmin", "from": "qwen2.5:14b"}`

Then edit the created model and set:

**System Prompt:**
```
You are a sysadmin assistant for this machine (Intel Arc iGPU, Linux/Docker homelab).
You have access to System Diagnostics and File Scout tools.
Always use these tools when asked about system status, health checks, loaded models, or infrastructure.
Never write code to simulate tool calls — always call the actual tool.
Be concise and technical.
```

**Tools:** enable System Diagnostics and File Scout

---

## 7. Add remote Ollama instances (optional)

If you have other machines running Ollama on your LAN, edit the `OLLAMA_INSTANCES` dict in `tools/system_diagnostics.py` and reinstall:

```python
OLLAMA_INSTANCES = {
    "local": "http://ollama-arc:11434",
    "remote1": "http://10.0.0.X:11434",   # your remote machine IP
}
```

Then update the tool in Open WebUI via **Admin Panel → Tools → System Diagnostics**.

---

## 8. Smart Model Router

The router runs as a filter pipeline. In v0.9.x it appears in the Pipelines valve settings at **Admin Panel → Settings → Pipelines**.

To enable debug mode (shows which model was selected):
- Expand `smart_model_router` in the Pipelines valves
- Set `debug` to `true`

To use the router in a chat, select any model — the router intercepts the request and overrides the model based on query content:

| Query type | Routed to |
|------------|-----------|
| Health checks, diagnostics, tool calls | `qwen2.5:14b` |
| Scripts, configs, code | `qwen2.5-coder:14b` |
| Root cause analysis, complex reasoning | `deepseek-r1:14b` |
| Log analysis, summaries, documentation | `gemma3:12b` |

---

## Troubleshooting

See `docs/troubleshooting.md` for common issues and solutions.
