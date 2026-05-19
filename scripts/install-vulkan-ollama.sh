#!/usr/bin/env bash
# install-vulkan-ollama.sh — install native Ollama with Vulkan/Mesa ANV GPU support
#
# For Intel iGPUs that predate Arc Alchemist (Iris Pro / Iris / UHD / Gen 9 etc.)
# where docker-compose.arc.yml (ipex-llm/SYCL) does NOT work.
#
# See docs/hardware/intel-igpu-vulkan.md for background and manual procedure.
# Validated 2026-05-19 on Intel NUC6i7KYB (Iris Pro 580) + lab1/2/3 NUC hardware.

set -euo pipefail


info()    { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
success() { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()    { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error()   { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

# ─── Prerequisites check ─────────────────────────────────────────────────────

info "Checking prerequisites..."

if ! command -v lspci &>/dev/null; then
    error "lspci not found. Install pciutils first: sudo apt install pciutils"
    exit 1
fi

if ! ls /dev/dri/renderD* &>/dev/null; then
    error "No /dev/dri/renderD* device found. Is the i915 kernel driver loaded?"
    error "Run: sudo lspci -v -s 00:02.0 | grep 'Kernel driver in use'"
    exit 1
fi

# Detect Intel iGPU
INTEL_GPU=$(lspci 2>/dev/null | grep -i "vga\|3d\|display" | grep -i "intel" | head -1 || true)
if [[ -z "$INTEL_GPU" ]]; then
    error "No Intel GPU detected via lspci. This script targets Intel iGPUs."
    exit 1
fi

info "Detected: $INTEL_GPU"

# Warn if this looks like an Arc GPU — arc.yml is likely the better path
if echo "$INTEL_GPU" | grep -iq "arc"; then
    warn "This looks like an Intel Arc GPU. docker-compose.arc.yml (ipex-llm/SYCL)"
    warn "may give better performance on Arc hardware. Continue anyway? [y/N]"
    read -r reply
    if [[ ! "$reply" =~ ^[Yy]$ ]]; then
        info "Aborted. See docs/hardware/arc.md for the Arc path."
        exit 0
    fi
fi

# ─── Stop conflicting Ollama (Docker or older native) ────────────────────────

info "Stopping any conflicting Ollama instance on port 11434..."

if command -v docker &>/dev/null; then
    CONTAINERS=$(docker ps -q --filter "publish=11434" 2>/dev/null || true)
    if [[ -n "$CONTAINERS" ]]; then
        info "Stopping Docker container(s) holding port 11434..."
        echo "$CONTAINERS" | xargs -r docker stop
    fi
fi

# Stop ai-stack systemd service if present (so it doesn't restart the container)
if systemctl list-unit-files 2>/dev/null | grep -q "^ai-stack.service"; then
    info "Stopping ai-stack.service so it doesn't restart the Docker Ollama..."
    sudo systemctl stop ai-stack.service || true
fi

# ─── Group memberships ───────────────────────────────────────────────────────

info "Adding $USER to render and video groups (required for /dev/dri access)..."
sudo usermod -aG render,video "$USER"

# ─── Install Ollama ──────────────────────────────────────────────────────────

if [[ -x /usr/local/bin/ollama ]]; then
    info "Existing Ollama install detected at /usr/local/bin/ollama."
    info "Will reuse the existing binary; only the systemd drop-in will change."
else
    info "Installing Ollama via official installer..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# ─── Vulkan systemd drop-in ──────────────────────────────────────────────────

info "Writing Vulkan systemd drop-in at /etc/systemd/system/ollama.service.d/override.conf..."

sudo mkdir -p /etc/systemd/system/ollama.service.d

sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_VULKAN=1"
Environment="RUSTICL_ENABLE=iris"
Environment="GPU_MAX_ALLOC_PERCENT=100"
Environment="OLLAMA_GPU_OVERHEAD=0"
EOF

# ─── Fix model directory ownership ───────────────────────────────────────────

if [[ -d /usr/share/ollama/.ollama ]]; then
    info "Ensuring /usr/share/ollama/.ollama is owned by ollama user..."
    sudo chown -R ollama:ollama /usr/share/ollama/.ollama/
fi

# ─── Reload + restart ────────────────────────────────────────────────────────

info "Reloading systemd and restarting Ollama..."
sudo systemctl daemon-reload
sudo systemctl enable ollama
sudo systemctl restart ollama

# ─── Verify ──────────────────────────────────────────────────────────────────

info "Waiting 5s for Ollama to come up..."
sleep 5

info "Checking journal for Vulkan inference-compute line..."
if sudo journalctl -u ollama --no-pager -n 100 2>/dev/null | grep -q "library=Vulkan"; then
    success "Vulkan GPU engaged. Journal confirms library=Vulkan."
    sudo journalctl -u ollama --no-pager -n 100 | grep "library=Vulkan" | head -2
else
    warn "No 'library=Vulkan' line found in recent journal."
    warn "This may mean the drop-in didn't take effect or the GPU isn't being used."
    warn "Check: sudo journalctl -u ollama --no-pager -n 100"
fi

# Check API
if curl -fsS http://127.0.0.1:11434/api/version &>/dev/null; then
    success "Ollama API responding at http://127.0.0.1:11434"
else
    error "Ollama API not responding. Check: sudo systemctl status ollama"
    exit 1
fi

# ─── Final guidance ──────────────────────────────────────────────────────────

cat <<'EOF'

────────────────────────────────────────────────────────────────────────────
Vulkan Ollama install complete.

Next steps:
  1. Pull a model:
     ollama pull qwen2.5:1.5b

  2. Verify GPU is used (run inference, then check):
     ollama run qwen2.5:1.5b "Hello"
     ollama ps
     # Expect PROCESSOR column to show "100% GPU"

  3. Start the rest of ai-stack (Olla, LiteLLM, Smart Router, Shepherd):
     docker compose up -d olla litellm router shepherd
     # (Native Ollama on port 11434 will be discovered as the local backend.)

If you switched from a Docker-based Ollama:
  - Models that were inside the container may need re-pulling, unless you
    pre-staged them under /usr/share/ollama/.ollama/models/.

Troubleshooting + background: docs/hardware/intel-igpu-vulkan.md
────────────────────────────────────────────────────────────────────────────
EOF
