# Troubleshooting

Lessons learned from building this stack. Check here before opening issues.

---

## GPU issues

### Models not using GPU / slow inference

**Symptom:** Inference is slow, `runner.vram="0 B"` in ollama logs, or `runner.inference` is not `oneapi`.

**Check:**
```bash
ls -la /dev/dri/
docker logs ollama-arc 2>&1 | grep -i "device\|gpu\|arc\|oneapi"
```

**Common causes:**

1. **Wrong card node** — On Meteor Lake/Arrow Lake, the Arc iGPU may be on `card0` or `card1` and this can change between reboots. The `check-arc-gpu.sh` script detects and updates `.env` automatically on each start.

   Manual fix:
   ```bash
   ls -la /dev/dri/
   cat /sys/class/drm/card0/device/vendor
   cat /sys/class/drm/card1/device/vendor
   ```

2. **Container started without GPU access** — If the card node drifted and the service started before `check-arc-gpu.sh` updated `.env`:
   ```bash
   sudo systemctl restart ai-stack.service
   ```

3. **Driver not loaded** — Check `lsmod | grep -E "i915|xe"`. If empty, the Intel GPU driver isn't loaded.

---

## Retriever

### Retriever shows 0 indexed files

**Check:**
```bash
curl localhost:42000/health
docker logs retriever --tail 20
```

**Common causes:**

1. **Vault path missing** — Verify `RETRIEVER_VAULT_PATH` in `.env` points to a real directory that contains `.md` files.

2. **Embeddings failing** — The retriever needs Olla healthy and `nomic-embed-text` pulled:
   ```bash
   curl localhost:40114/internal/health
   docker exec ollama-arc ollama list | grep nomic
   docker exec ollama-arc ollama pull nomic-embed-text:latest
   ```

3. **Vault not mounted** — Check the container:
   ```bash
   docker exec retriever ls /vault
   ```

---

## Olla

### Olla not routing correctly

**Check:**
```bash
curl localhost:40114/internal/status/endpoints
```

**Fix:** Regenerate config after changing `.env`:
```bash
bash scripts/generate-olla-config.sh
sudo systemctl restart ai-stack.service
```

---

## LiteLLM

### LiteLLM won't start / cloud models unavailable

**Check logs:**
```bash
docker logs litellm --tail 30
```

**Common causes:**

1. **Missing API keys** — `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` must be set in `.env`. LiteLLM will start without them but cloud models won't be available.

2. **Healthcheck** — LiteLLM's liveness endpoint is at `/health/liveness` (not `/health`). The healthcheck in compose uses the correct URL.

---

### Remote instance unreachable

**Symptom:** Olla reports a remote node as unreachable.

**Check:**
```bash
curl http://192.168.1.X:11434/api/tags
```

**Fix:** Ensure the remote host is reachable from the Docker network. If the host is on the LAN, Olla should be able to reach it. For host-local addresses, you may need `extra_hosts`:

```yaml
olla:
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

---

## `docker compose down` fails with "invalid hostPort"

**Cause:** A port mapping in the compose file has a typo.

**Fix:** Stop containers by name:
```bash
docker stop ollama-arc litellm olla retriever
docker rm ollama-arc litellm olla retriever
```
Then fix the typo in `docker-compose.yml` and restart.

---

## Service fails on boot

**Check:**
```bash
sudo systemctl status ai-stack.service
journalctl -xeu ai-stack.service
```

**Common causes:**
1. Docker not ready yet — the `After=docker.service` dependency usually handles this, but on slow systems add `sleep 5` to ExecStartPre.
2. GPU pre-flight failed — check `check-arc-gpu.sh` output in the journal.
3. Port conflict — another service is using one of your configured ports.
