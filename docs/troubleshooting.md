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
   # Find which card is Intel (vendor 0x8086)
   cat /sys/class/drm/card0/device/vendor
   cat /sys/class/drm/card1/device/vendor
   # Update .env with the correct card
   ```

2. **Container started without GPU access** — If the card node drifted and the service started before `check-arc-gpu.sh` updated `.env`:
   ```bash
   sudo systemctl restart ai-stack.service
   ```

3. **Driver not loaded** — Check `lsmod | grep -E "i915|xe"`. If empty, the Intel GPU driver isn't loaded.

---

## Ollama not reachable from Open WebUI

**Symptom:** "Trouble accessing Ollama" warning in Open WebUI connections.

**Check:**
```bash
docker exec -it open-webui curl http://ollama-arc:11434/api/tags
```

**Fix:** Verify both containers are on the `ai-net` network:
```bash
docker network inspect ai-stack_ai-net | grep -A3 "Name"
```

---

## Remote Ollama instance unreachable from tools

**Symptom:** System Diagnostics reports remote instance as unreachable, but you can ping/curl it from the host.

**Root cause:** Tools run inside the Open WebUI container's Python process. The container needs a route to your LAN.

**Check:**
```bash
docker exec -it open-webui python3 -c "
import httpx, asyncio
async def test():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get('http://YOUR_REMOTE_IP:11434/api/tags')
        print('OK', r.status_code)
asyncio.run(test())
"
```

**Fix:** If the above fails, add `extra_hosts` to open-webui in `docker-compose.yml`:
```yaml
open-webui:
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

---

## Pipelines crash loop

**Symptom:** `docker logs pipelines` shows repeated startup failures.

**Most common cause:** A pipeline file is missing required configuration (e.g. a GitHub token).

**Fix:**
```bash
# Find the offending pipeline
docker logs pipelines 2>&1 | grep "ERROR\|ValueError"

# Remove it
docker exec pipelines rm -rf /app/pipelines/PROBLEM_PIPELINE.py
docker exec pipelines rm -rf /app/pipelines/PROBLEM_PIPELINE/
docker exec pipelines rm -rf /app/pipelines/__pycache__
docker restart pipelines
```

---

## Pipeline changes not taking effect

**Symptom:** Updated a `.py` file but old behaviour persists.

**Cause:** Python bytecode cache (`__pycache__`) is stale.

**Fix:** Always clear cache after updating pipeline files:
```bash
docker exec pipelines rm -rf /app/pipelines/__pycache__
docker restart pipelines
```

---

## Models not showing in Open WebUI after restart

**Symptom:** Chat model selector is empty after system reboot.

**Cause:** Open WebUI caches the model list and the cache can go stale.

**Fix:**
```bash
docker restart open-webui
```
Then refresh the browser.

---

## `docker compose down` fails with "invalid hostPort"

**Cause:** A port mapping in the compose file has a typo (e.g. `" :8000"` instead of `"8000:8000"`).

**Fix:** Stop containers by name since compose can't parse the file:
```bash
docker stop open-webui open-terminal pipelines ollama-arc
docker rm open-webui open-terminal pipelines ollama-arc
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

---

## open-webui volume issues after migration

If you moved from a separate compose setup, the `open-webui` volume is marked `external: true` and must exist before the stack starts:

```bash
docker volume create open-webui
```
