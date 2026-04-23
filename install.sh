#!/usr/bin/env bash
# ─── install.sh ───────────────────────────────────────────────────────────────
# AI Stack installer
# Installs a self-hosted AI stack optimised for Intel Arc iGPU on Linux
#
# Requirements:
#   - Ubuntu 22.04+ or Debian 12+ (tested on Ubuntu 24.04)
#   - Docker + Docker Compose plugin
#   - Intel Arc GPU with i915/xe driver loaded
#   - User in docker group
#
# Usage:
#   cp .env.example .env && nano .env   # configure first
#   ./install.sh
#
# NOTE: For best Intel Arc GPU performance, also install:
#   sudo apt install intel-opencl-icd intel-media-va-driver-non-free libmfx1

set -euo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}${BLUE}═══ $* ═══${RESET}\n"; }

# ─── Load .env ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    error ".env not found. Run: cp .env.example .env && nano .env"
fi

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.env"

STACK_USER="${STACK_USER:-$(whoami)}"
INSTALL_DIR="${INSTALL_DIR:-${SCRIPT_DIR}}"

# ─── Preflight checks ─────────────────────────────────────────────────────────
header "Preflight Checks"

check_docker() {
    if ! command -v docker &>/dev/null; then
        error "Docker not found. Install: https://docs.docker.com/engine/install/"
    fi
    if ! docker compose version &>/dev/null; then
        error "Docker Compose plugin not found. Install the docker-compose-plugin package."
    fi
    success "Docker $(docker --version | awk '{print $3}' | tr -d ',')"
    success "Docker Compose $(docker compose version --short)"
}

check_docker_group() {
    if ! groups "${STACK_USER}" | grep -q docker; then
        warn "User ${STACK_USER} is not in the docker group."
        warn "Run: sudo usermod -aG docker ${STACK_USER} && newgrp docker"
        warn "Then re-run this installer."
        exit 1
    fi
    success "User ${STACK_USER} is in the docker group."
}

check_intel_gpu() {
    if ! ls /dev/dri/card* &>/dev/null; then
        error "No DRI devices found. Is the Intel GPU driver loaded?"
    fi

    local found=false
    for card in /dev/dri/card*; do
        local cardnum="${card##*card}"
        local vendor
        vendor=$(cat "/sys/class/drm/card${cardnum}/device/vendor" 2>/dev/null || echo "")
        if [[ "$vendor" == "0x8086" ]]; then
            success "Intel Arc GPU found: ${card}"
            found=true
            sed -i "s|^GPU_CARD=.*|GPU_CARD=${card}|" "${SCRIPT_DIR}/.env"
            info "Updated .env: GPU_CARD=${card}"
            break
        fi
    done

    if [[ "$found" == "false" ]]; then
        error "No Intel GPU (vendor 0x8086) found in /dev/dri/. Check driver installation."
    fi

    if [[ ! -e "/dev/dri/renderD128" ]]; then
        error "Render node /dev/dri/renderD128 not found."
    fi
    success "Render node /dev/dri/renderD128 present."
}

check_memory() {
    local total_kb
    total_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    local total_gb=$(( total_kb / 1024 / 1024 ))
    if (( total_gb < 16 )); then
        warn "Only ${total_gb}GB RAM detected. 32GB recommended for 14b models."
    else
        success "${total_gb}GB RAM available."
    fi
}

check_obsidian_vault() {
    local vault_path="${OBSIDIAN_VAULT_PATH:-}"
    if [[ -z "$vault_path" ]]; then
        warn "OBSIDIAN_VAULT_PATH not set in .env — Khoj will start but vault indexing will be disabled."
        warn "Set OBSIDIAN_VAULT_PATH in .env and restart to enable vault search."
    elif [[ ! -d "$vault_path" ]]; then
        warn "OBSIDIAN_VAULT_PATH=${vault_path} does not exist — Khoj will start but vault won't be mounted."
        warn "Create the directory or fix the path in .env."
    else
        success "Obsidian vault found: ${vault_path}"
    fi
}

check_docker
check_docker_group
check_intel_gpu
check_memory
check_obsidian_vault

# ─── Create docker volumes ────────────────────────────────────────────────────
header "Docker Volumes"

if ! docker volume inspect open-webui &>/dev/null; then
    docker volume create open-webui
    success "Created docker volume: open-webui"
else
    success "Docker volume open-webui already exists."
fi

# ─── Install systemd service ──────────────────────────────────────────────────
header "Systemd Service"

SERVICE_SRC="${SCRIPT_DIR}/systemd/ai-stack.service"
SERVICE_DST="/etc/systemd/system/ai-stack.service"

sed \
    -e "s|\${INSTALL_DIR}|${INSTALL_DIR}|g" \
    -e "s|\${STACK_USER}|${STACK_USER}|g" \
    "${SERVICE_SRC}" | sudo tee "${SERVICE_DST}" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable ai-stack.service
success "Installed and enabled ai-stack.service"

# ─── Install pipelines ────────────────────────────────────────────────────────
# NOTE: Only pipeline .py files are deployed here (pipelines/ directory).
# Tools (System Diagnostics etc.) live in Open WebUI's database and must be
# added via post-install.sh or manually via Admin Panel → Tools.
header "Installing Pipelines"

install_pipelines() {
    info "Starting pipelines container to install pipeline files..."

    cd "${INSTALL_DIR}"
    docker compose up -d pipelines
    sleep 5

    # Clear pycache to avoid stale bytecode
    docker exec pipelines rm -rf /app/pipelines/__pycache__ 2>/dev/null || true

    local installed=0
    for f in "${SCRIPT_DIR}/pipelines/"*.py; do
        [[ -f "$f" ]] || continue
        docker cp "$f" "pipelines:/app/pipelines/$(basename "$f")"
        success "Installed pipeline: $(basename "$f")"
        (( installed++ )) || true
    done

    if (( installed == 0 )); then
        warn "No pipeline files found in ${SCRIPT_DIR}/pipelines/ — skipping."
    fi

    docker exec pipelines rm -rf /app/pipelines/__pycache__ 2>/dev/null || true
    docker restart pipelines
    sleep 3

    docker logs pipelines --tail 10 | grep -E "Loaded module|ERROR" || true
    success "Pipelines installed and restarted."
}

install_pipelines

# ─── Start the full stack ─────────────────────────────────────────────────────
header "Starting AI Stack"

sudo systemctl start ai-stack.service
sleep 5

if sudo systemctl is-active --quiet ai-stack.service; then
    success "ai-stack.service is running."
else
    error "ai-stack.service failed to start. Run: journalctl -xeu ai-stack.service"
fi

# ─── Pull models ──────────────────────────────────────────────────────────────
header "Pulling Models"

MODELS_TO_PULL="${MODELS_TO_PULL:-deepseek-r1:14b qwen2.5-coder:14b gemma3:12b qwen2.5:14b nomic-embed-text:latest}"

info "This will pull: ${MODELS_TO_PULL}"
info "This may take a while depending on your connection speed."
read -rp "Pull models now? [y/N] " pull_models

if [[ "${pull_models,,}" == "y" ]]; then
    # Find ollama binary inside container — path varies by image
    OLLAMA_BIN=$(docker exec ollama-arc sh -c \
        'which ollama 2>/dev/null || find /usr /llm -name ollama -type f -executable 2>/dev/null | head -n 1')

    if [[ -z "$OLLAMA_BIN" ]]; then
        error "Could not find ollama binary inside ollama-arc container."
    fi

    info "Found ollama at: ${OLLAMA_BIN}"

    for model in ${MODELS_TO_PULL}; do
        info "Pulling ${model}..."
        docker exec ollama-arc "${OLLAMA_BIN}" pull "${model}" \
            && success "Pulled: ${model}" \
            || warn "Failed to pull: ${model} (check container logs)"
    done
else
    info "Skipping model pull. Pull manually with:"
    for model in ${MODELS_TO_PULL}; do
        echo "  docker exec ollama-arc ollama pull ${model}"
    done
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
header "Installation Complete"

WEBUI_PORT="${WEBUI_PORT:-3000}"
KHOJ_PORT="${KHOJ_PORT:-42110}"

echo -e "${GREEN}${BOLD}Stack is running!${RESET}"
echo ""
echo -e "  Open WebUI:  ${BOLD}http://localhost:${WEBUI_PORT}${RESET}"
echo -e "  Ollama API:  ${BOLD}http://localhost:${OLLAMA_PORT:-11434}${RESET}"
echo -e "  Pipelines:   ${BOLD}http://localhost:${PIPELINES_PORT:-9099}${RESET}"
echo -e "  Khoj:        ${BOLD}http://localhost:${KHOJ_PORT}${RESET}"
echo ""
echo -e "${YELLOW}Next steps:${RESET}"
echo ""
echo -e "  1. Run ${BOLD}./post-install.sh${RESET} to auto-configure Open WebUI"
echo -e "  2. Follow ${BOLD}docs/khoj-setup.md${RESET} to connect Obsidian to Khoj"
echo ""
echo -e "  Full guide: ${BOLD}docs/post-install.md${RESET}"
