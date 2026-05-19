# Older Intel iGPU — Vulkan via Mesa ANV (native Ollama)

For Intel iGPUs that **predate the Arc Alchemist generation** (Iris Pro 580 / Iris / UHD / Skylake-era Gen 9 etc.), the
`docker-compose.arc.yml` overlay (ipex-llm + SYCL) does **not** work — the ipex-llm runtime requires 11th-Gen Core+
class hardware. Use the **Vulkan path via Mesa ANV** with a **native (non-Docker) Ollama install**.

This was validated 2026-05-19 on:

- **nuk1** — Intel NUC6i7KYB ("Skull Canyon"), Iris Pro 580 (Gen 9 GT4e, 72 EUs, 128 MB eDRAM), 32 GB RAM
- **lab1, lab2, lab3** — similar Intel NUC hardware class

After this procedure: `ollama ps` shows `100% GPU`, default context jumps from 4096 → 32768, and the GPU is visible
as `library=Vulkan name=Vulkan0 description="Intel(R) Iris(R) Pro Graphics 580" total="23.4 GiB"` (or similar).

## Why this path (not arc.yml)

| Hardware class | Path | Why |
|---|---|---|
| Intel Arc (Alchemist / Battlemage, 12th-Gen+) | `docker-compose.arc.yml` | ipex-llm/SYCL stack tested and supported on this hardware |
| Intel Iris Xe (Gen 12, 11th-Gen Core+) | `docker-compose.arc.yml` *or* this path | Either stack works; Vulkan is broader-supported, ipex-llm is more optimized when it works |
| Intel Iris Pro / Iris / UHD / Gen 9 or older | **this path** (Vulkan) | ipex-llm/SYCL doesn't support pre-11th-Gen iGPUs; Vulkan via Mesa ANV does |
| NVIDIA | `docker-compose.nvidia.yml` | n/a |
| CPU-only | base `docker-compose.yml` | fallback |

The rest of ai-stack (Olla, LiteLLM, Smart Router, Shepherd) runs in Docker as usual; only the Ollama service runs natively,
exposing port 11434 to the localhost where the other services connect.

## Prerequisites

- Ubuntu 24.04 LTS (Noble) or compatible
- `i915` kernel driver active (`lspci -k -s 00:02.0 | grep "Kernel driver in use: i915"`)
- `/dev/dri/renderD128` present (`ls /dev/dri/`)
- User in `render` and `video` groups (the Ollama installer adds these and creates the `ollama` user automatically)

## Procedure

The procedure is automated by `scripts/install-vulkan-ollama.sh` — see that script for the full sequence. Manual steps below
are for operators who want to understand or adapt each step.

### 1. Verify GPU + driver

```bash
lspci -nn | grep -i vga
# Expected: Intel ... Iris ... [8086:...]

sudo lspci -v -s 00:02.0 | grep "Kernel driver"
# Expected: Kernel driver in use: i915

ls /dev/dri/
# Expected: card0 or card1, renderD128
```

### 2. Stop any conflicting Ollama (Docker or older native)

If you previously ran Ollama via `docker-compose.arc.yml` or any other Docker container, it holds port 11434 and
must be stopped:

```bash
# If running via ai-stack:
sudo systemctl stop ai-stack.service 2>/dev/null || true

# Or stop the Docker Ollama container directly:
docker ps -q --filter "publish=11434" | xargs -r docker stop
```

### 3. Add yourself to render/video groups

```bash
sudo usermod -aG render,video "$USER"
# Log out and back in, OR apply to current shell:
newgrp render
```

### 4. Install Ollama (native, via official installer)

```bash
# Optional: clean previous broken install
sudo rm -f /usr/local/bin/ollama
sudo systemctl disable ollama 2>/dev/null || true
sudo rm -f /etc/systemd/system/ollama.service

# Fresh install
curl -fsSL https://ollama.com/install.sh | sh
```

The installer creates the `ollama` user, adds it to `render` + `video` groups, and starts the systemd service.

### 5. Add the Vulkan systemd drop-in

By default the installer warns "No NVIDIA/AMD GPU detected" and falls back to CPU-only. Override with a drop-in:

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_VULKAN=1"
Environment="RUSTICL_ENABLE=iris"
Environment="GPU_MAX_ALLOC_PERCENT=100"
Environment="OLLAMA_GPU_OVERHEAD=0"
EOF

sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### 6. Fix model directory permissions

If models were previously downloaded by a different user (e.g. a Docker-based setup with a `root`-owned bind-mount),
fix ownership so the `ollama` user can read them:

```bash
sudo chown -R ollama:ollama /usr/share/ollama/.ollama/
```

### 7. Verify GPU is engaged

```bash
sudo journalctl -u ollama --no-pager -n 50 | grep -i "inference compute"
```

You want to see a line like:

```
inference compute id=... library=Vulkan name=Vulkan0
description="Intel(R) Iris(R) Pro Graphics 580 (SKL GT4)"
type=iGPU total="23.4 GiB" available="13.8 GiB"
```

And the default context should have jumped to 32768 (vs 4096 in CPU mode):

```
vram-based default context  total_vram="23.4 GiB"  default_num_ctx=32768
```

### 8. Test inference

```bash
ollama pull qwen2.5:1.5b
ollama run qwen2.5:1.5b "Hello"
ollama ps
```

Expected output of `ollama ps`:

```
NAME            ID    SIZE    PROCESSOR    CONTEXT
qwen2.5:1.5b    ...   2.8 GB  100% GPU     32768
```

`100% GPU` is the success indicator. Anything less means the model partially fell back to CPU — usually because
the model is larger than available VRAM.

## ai-stack integration

The Olla / LiteLLM / Smart Router / Shepherd services in `docker-compose.yml` connect to Ollama at `localhost:11434` —
whether Ollama runs in Docker or natively, they don't care. After this procedure:

```bash
# Start the rest of ai-stack (Ollama already running natively):
docker compose up -d olla litellm router shepherd
```

Or use the same `start.sh` ai-stack provides — set `GPU_TYPE=vulkan` (or `cpu`, since the compose file doesn't need
to know about the native Vulkan Ollama) in `.env`.

## Troubleshooting

### `journalctl -u ollama` shows CPU-only / "No GPU detected"

The drop-in didn't take effect. Verify:

```bash
sudo systemctl show ollama -p Environment | tr ' ' '\n' | grep -i ollama
# Expected lines: Environment=OLLAMA_VULKAN=1 RUSTICL_ENABLE=iris ...
```

If missing, the drop-in file isn't being read. Confirm `/etc/systemd/system/ollama.service.d/override.conf` exists and
re-run `sudo systemctl daemon-reload && sudo systemctl restart ollama`.

### `ollama ps` shows `100% CPU` even though Vulkan is engaged

Model size exceeds VRAM. Either:
- Use a smaller quantization
- Use a smaller model
- Check `available` VRAM in the journal line — 14 GiB free means models <14 GiB fit fully

### `vulkaninfo` hangs

Common on headless systems (no display). Either skip the check or run from a second SSH session.
The `journalctl` line is the authoritative signal — if it says `library=Vulkan`, Vulkan is working.

### Permission denied on `/dev/dri/renderD128`

User isn't in the `render` group. Re-check step 3 and log out / back in (or `newgrp render` in the current shell).

## Hardware-class boundary

This path is for Intel iGPUs **without** ipex-llm/SYCL support — primarily Gen 9 (Skylake era) and older.
For 11th-Gen Core+ / Iris Xe / Arc you can also use this path, but `docker-compose.arc.yml` may give better
performance on supported hardware. Run benchmarks on both paths if you're not sure.

The ipex-llm tested-and-supported hardware floor is documented by Intel as
**iGPUs of 11th Gen Core and newer have been tested; older iGPU works but with poor performance**
([source](https://github.com/intel/ipex-llm) — note: project archived 2026-01-28). For older iGPUs this Vulkan path
is the supported community direction.

## Background

Empirically validated 2026-05-19 by NetYeti on Intel NUC6i7KYB (Iris Pro 580) using free-tier Claude.ai in under
one hour, after the ai-stack pod spent ~10 hours on a parallel SYCL/ipex-llm investigation that concluded the
hardware was "GPU-incapable" (an over-generalization — the hardware is fine; ipex-llm just doesn't support this
generation). Lesson banked: single-stack failure ≠ hardware-class failure. See `docs/hardware/arc.md` for the
Arc path; this file documents the Iris/older-iGPU path.
