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

# Resolve VaultWarden placeholders before sourcing
if grep -q '<vaultwarden:' "${SCRIPT_DIR}/.env" 2>/dev/null; then
    info "Resolving VaultWarden placeholders in .env..."
    if [[ -f "${SCRIPT_DIR}/scripts/resolve-vaultwarden.sh" ]]; then
        bash "${SCRIPT_DIR}/scripts/resolve-vaultwarden.sh"
    else
        warn "resolve-vaultwarden.sh not found, sourcing .env as-is"
    fi
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

check_vault() {
    local vault_path="${RETRIEVER_VAULT_PATH:-}"
    if [[ -z "$vault_path" ]]; then
        warn "RETRIEVER_VAULT_PATH not set in .env — retriever will start but vault indexing will be disabled."
    elif [[ ! -d "$vault_path" ]]; then
        warn "RETRIEVER_VAULT_PATH=${vault_path} does not exist."
        read -rp "Create this directory now? [Y/n] " create_vault
        if [[ ! "${create_vault,,}" =~ ^n ]]; then
            mkdir -p "$vault_path"
            success "Created vault directory: ${vault_path}"
        else
            warn "Vault directory not created — retriever will start but vault won't be mounted."
        fi
    else
        success "Obsidian vault found: ${vault_path}"
    fi
}

check_docker
check_docker_group
check_intel_gpu
check_memory
check_vault

# ─── Create docker volumes ────────────────────────────────────────────────────
header "Docker Volumes"

if ! docker volume inspect ai-stack_retriever-data &>/dev/null; then
    docker volume create ai-stack_retriever-data
    success "Created docker volume: retriever-data"
else
    success "Docker volume retriever-data already exists."
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

# OpenCode (CLI + Obsidian sidebar plugin) is the primary AI interface.

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

MODELS_TO_PULL="${MODELS_TO_PULL:-qwen2.5:1.5b deepseek-r1:14b gemma4:27b mistral-small3.2:24b qwen3.5:14b qwen2.5-coder:14b gemma3:12b qwen2.5:14b nomic-embed-text:latest}"

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
        if docker exec ollama-arc "${OLLAMA_BIN}" pull "${model}"; then
            success "Pulled: ${model}"
        else
            warn "Failed to pull: ${model} (check container logs)"
        fi
    done
else
    info "Skipping model pull. Pull manually with:"
    for model in ${MODELS_TO_PULL}; do
        echo "  docker exec ollama-arc ollama pull ${model}"
    done
fi

# ─── Install OpenCode ─────────────────────────────────────────────────────────
header "OpenCode CLI"

if command -v opencode &>/dev/null; then
    success "OpenCode already installed ($(opencode --version 2>/dev/null || echo 'unknown version'))"
else
    info "OpenCode is the primary AI interface for this stack."
    read -rp "Install OpenCode now? [Y/n] " install_oc
    if [[ ! "${install_oc,,}" =~ ^n ]]; then
        if command -v npm &>/dev/null; then
            info "Installing via npm..."
            npm install -g opencode-ai
        elif command -v bun &>/dev/null; then
            info "Installing via bun..."
            bun install -g opencode-ai
        else
            info "Installing via install script..."
            curl -fsSL https://opencode.ai/install | bash
        fi
        if command -v opencode &>/dev/null; then
            success "OpenCode installed."
        else
            warn "OpenCode installation may need manual steps. See https://opencode.ai/docs"
        fi
    else
        info "Skipping OpenCode install. Install later: curl -fsSL https://opencode.ai/install | bash"
    fi
fi

# ─── Install Bun (needed by OpenCode Obsidian plugin) ─────────────────────────
header "Bun Runtime"

if command -v bun &>/dev/null; then
    success "Bun already installed ($(bun --version 2>/dev/null || echo 'unknown version'))"
else
    info "Bun is required by the OpenCode Obsidian plugin."
    read -rp "Install Bun now? [Y/n] " install_bun
    if [[ ! "${install_bun,,}" =~ ^n ]]; then
        info "Installing Bun..."
        curl -fsSL https://bun.sh/install | bash
        if command -v bun &>/dev/null; then
            success "Bun installed."
        else
            warn "Bun installed but may need a new shell session or PATH update."
        fi
    else
        info "Skipping Bun install. Install later: curl -fsSL https://bun.sh/install | bash"
    fi
fi

# ─── Configure OpenCode with stack providers ──────────────────────────────────
header "OpenCode Configuration"

OC_CONFIG_DIR="${HOME}/.config/opencode"
OC_CONFIG="${OC_CONFIG_DIR}/opencode.json"
PROJECT_OC_CONFIG="${SCRIPT_DIR}/opencode.json"

if command -v opencode &>/dev/null; then
    mkdir -p "${OC_CONFIG_DIR}"

    # Global config — providers, models, permissions
    if [[ -f "${OC_CONFIG}" ]]; then
        success "OpenCode global config already exists at ${OC_CONFIG}"
    else
        info "Creating global OpenCode config with stack providers..."
        cat > "${OC_CONFIG}" << OCEOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "olla/qwen3.5:14b",
  "provider": {
    "olla": {
      "npm": "@ai-sdk/openai-compatible",
          "name": "Olla (local Ollama cluster)",
          "options": {
            "baseURL": "http://localhost:40115/v1"
          },
          "models": {
            "auto": {
              "name": "Auto-select (Smart Router)",
              "tools": true
            },
            "qwen2.5:14b": {
              "name": "Qwen 2.5 14B (diagnostics)",
              "tools": true
            },
            "qwen2.5-coder:14b": {
              "name": "Qwen 2.5 Coder 14B (code)",
              "tools": true
            },
            "deepseek-r1:14b": {
              "name": "DeepSeek R1 14B (reasoning)",
              "tools": true
            },
            "gemma3:12b": {
              "name": "Gemma 3 12B (longform)",
              "tools": true
            },
            "gemma4:27b": {
              "name": "Gemma 4 27B (heavy lifting)",
              "tools": true
            },
            "mistral-small3.2:24b": {
              "name": "Mistral Small 3.2 24B (tool calling)",
              "tools": true
            },
            "nomic-embed-text": {
              "name": "Nomic Embed Text (embeddings)"
            }
          }
    },
    "litellm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "LiteLLM (cloud models)",
      "options": {
        "baseURL": "http://localhost:4000/v1"
      },
      "models": {
        "claude-sonnet-4-20250514": {
          "name": "Claude Sonnet 4 (Anthropic)",
          "tools": true
        },
        "gemini-2.0-flash-001": {
          "name": "Gemini 2.0 Flash (Google)",
          "tools": true
        }
      }
    }
  },
  "permission": {
    "bash": "ask",
    "edit": "ask",
    "write": "ask"
  }
}
OCEOF
        success "Created OpenCode global config at ${OC_CONFIG}"
        info "You can add more models by editing ${OC_CONFIG}"
    fi

    # Project-level config — instructions injected from AGENTS.md
    if [[ ! -f "${PROJECT_OC_CONFIG}" ]]; then
        cat > "${PROJECT_OC_CONFIG}" << 'OCEOF'
{
  "$schema": "https://opencode.ai/config.json",
  "instructions": [
    "AGENTS.md"
  ]
}
OCEOF
        success "Created project OpenCode config at ${PROJECT_OC_CONFIG}"
    else
        success "Project OpenCode config already exists at ${PROJECT_OC_CONFIG}"
    fi
fi

# ─── Install OpenCode Obsidian plugin ──────────────────────────────────────────
header "OpenCode Obsidian Plugin"

info "The OpenCode Obsidian plugin embeds the AI assistant in your sidebar."
info "It needs to be installed in this vault's .obsidian/plugins directory."

if command -v opencode &>/dev/null && command -v bun &>/dev/null; then
    PLUGIN_DIR="${SCRIPT_DIR}/.obsidian/plugins/obsidian-opencode"
    if [[ -d "${PLUGIN_DIR}" ]]; then
        success "OpenCode Obsidian plugin already installed"
    else
        info "Cloning opencode-obsidian plugin..."
        mkdir -p "${SCRIPT_DIR}/.obsidian/plugins"
        if git clone https://github.com/growlf/opencode-obsidian.git "${PLUGIN_DIR}" 2>/dev/null; then
            info "Building plugin..."
            if (cd "${PLUGIN_DIR}" && bun install && bun run build) 2>/dev/null; then
                success "OpenCode Obsidian plugin installed and built."
                # Auto-enable in community-plugins.json
                echo '["opencode-obsidian"]' > "${SCRIPT_DIR}/.obsidian/community-plugins.json"
                success "Plugin enabled. Restart Obsidian to see the sidebar icon."
            else
                warn "Plugin build failed. Check Bun installation."
                rm -rf "${PLUGIN_DIR}"
            fi
        else
            warn "Failed to clone plugin repo. Check internet connection."
        fi
    fi
else
    warn "OpenCode CLI or Bun not installed — skipping plugin setup."
    info "Install both first, then run:"
    info "  git clone https://github.com/growlf/opencode-obsidian.git .obsidian/plugins/obsidian-opencode"
    info "  cd .obsidian/plugins/obsidian-opencode && bun install && bun run build"
fi

# ─── Bitwarden / VaultWarden Secret Management (optional) ──────────────────────
header "Bitwarden / VaultWarden"

info "The stack can resolve <vaultwarden:path> placeholders in .env"
info "using Bitwarden (or self-hosted VaultWarden) for secret management."
info "This lets you store API keys in your vault instead of plaintext in .env."
echo ""

read -rp "Configure Bitwarden secret management? [y/N] " setup_bw
if [[ "${setup_bw,,}" != "y" ]]; then
    info "Skipping Bitwarden setup."
else
    # ── Check for existing session ─────────────────────────────────────────
    BW_HAS_SESSION=false
    if command -v bw &>/dev/null; then
        bw_status=$(bw status 2>/dev/null || echo '{"status":"unauthenticated"}')
        if echo "$bw_status" | grep -q '"status":"unlocked"'; then
            BW_HAS_SESSION=true
            success "Bitwarden vault already unlocked."
        fi
    fi

    # ── Install bw CLI if missing ──────────────────────────────────────────
    if ! command -v bw &>/dev/null; then
        info "Installing Bitwarden CLI via npm..."
        if ! command -v npm &>/dev/null; then
            info "npm not found — installing Node.js..."
            if command -v snap &>/dev/null; then
                sudo snap install node --classic
            elif command -v apt-get &>/dev/null; then
                sudo apt-get update -qq && sudo apt-get install -y -qq nodejs npm
            else
                warn "Cannot install npm automatically."
                info "Install Node.js manually, then run: npm install -g @bitwarden/cli"
            fi
        fi
        if command -v npm &>/dev/null; then
            npm install -g @bitwarden/cli
            if command -v bw &>/dev/null; then
                success "Bitwarden CLI installed."
            else
                warn "bw CLI install may need a new shell or PATH update."
            fi
        fi
    fi

    if ! command -v bw &>/dev/null; then
        warn "bw CLI not available — skipping Bitwarden configuration."
        info "Install manually: npm install -g @bitwarden/cli"
    elif [[ "$BW_HAS_SESSION" != "true" ]]; then
        # ── Server URL (self-hosted VaultWarden) ─────────────────────────
        echo ""
        info "Are you using Bitwarden cloud (bitwarden.com) or a self-hosted VaultWarden?"
        read -rp "Self-hosted VaultWarden URL (or leave blank for Bitwarden cloud): " BW_SERVER_URL_VAL
        if [[ -n "${BW_SERVER_URL_VAL}" ]]; then
            if [[ "${BW_SERVER_URL_VAL,,}" != https://* ]]; then
                warn "URL must use HTTPS. Prepending https://"
                BW_SERVER_URL_VAL="https://${BW_SERVER_URL_VAL}"
            fi
            bw config server "$BW_SERVER_URL_VAL" >/dev/null 2>&1
            success "VaultWarden server configured: ${BW_SERVER_URL_VAL}"
        fi

        # ── Login ────────────────────────────────────────────────────────
        echo ""
        info "Log in to Bitwarden now. Your master password is used only for this"
        info "one-time login and will NOT be stored anywhere."
        read -rp "Bitwarden email: " BW_EMAIL
        read -rsp "Master password (not stored): " BW_MASTER_PW
        echo ""

        export BW_CLIENT_ID=""
        export BW_CLIENT_SECRET=""
        BW_SESSION=$(echo "$BW_MASTER_PW" | bw login "$BW_EMAIL" --raw 2>/dev/null || true)
        BW_MASTER_PW=""
        if [[ -z "$BW_SESSION" ]]; then
            warn "Login failed. You may have 2FA enabled."
            info "Run 'bw login $BW_EMAIL' manually in another terminal, then re-run install.sh."
        else
            success "Logged in as ${BW_EMAIL}."
            export BW_SESSION
            bw sync >/dev/null 2>&1
        fi
    fi

    if command -v bw &>/dev/null; then
        # ── Organization ID ──────────────────────────────────────────────
        echo ""
        info "You need a Bitwarden organization ID to scope secret lookups."
        info "Find it by logging into the Bitwarden web vault → Settings → Organizations."
        echo ""
        read -rp "Bitwarden Organization ID (leave blank to skip): " BW_ORG_ID

        if [[ -n "${BW_ORG_ID}" ]]; then
            # ── API key setup ───────────────────────────────────────────
            echo ""
            info "Generate a Bitwarden API key for non-interactive secret resolution:"
            info "  Web vault → Settings → Security → Keys tab → View API Key"
            info "  (Enter your master password to view, then copy the values.)"
            echo ""
            read -rp "BW_CLIENT_ID (e.g. user.xxxxxx): " BW_CLIENT_ID_VAL
            read -rsp "BW_CLIENT_SECRET: " BW_CLIENT_SECRET_VAL
            echo ""

            # Remove any existing LITELLM_MASTER_KEY from .env (avoid duplicates)
            if grep -q '^LITELLM_MASTER_KEY=' "${SCRIPT_DIR}/.env" 2>/dev/null; then
                sed -i '/^LITELLM_MASTER_KEY=/d' "${SCRIPT_DIR}/.env"
                info "Removed existing LITELLM_MASTER_KEY from .env (will be replaced)."
            fi

            # ── Write to .env ───────────────────────────────────────────
            if [[ -n "${BW_SERVER_URL_VAL:-}" ]]; then
                echo "BW_SERVER_URL=${BW_SERVER_URL_VAL}" >> .env
            fi
            {
                echo ""
                echo "# ─── Bitwarden / VaultWarden (added by install.sh) ─────────────────"
                echo "BW_CLIENT_ID=${BW_CLIENT_ID_VAL}"
                echo "BW_CLIENT_SECRET=${BW_CLIENT_SECRET_VAL}"
                echo ""
                echo "# Secrets stored in Bitwarden — resolved via resolve-vaultwarden.sh"
                echo "# Format: <vaultwarden:org-id/item-name>"
                echo "ANTHROPIC_API_KEY=<vaultwarden:${BW_ORG_ID}/anthropic-api-key>"
                echo "GEMINI_API_KEY=<vaultwarden:${BW_ORG_ID}/gemini-api-key>"
                echo "LITELLM_MASTER_KEY=<vaultwarden:${BW_ORG_ID}/litellm-master-key>"
            } >> .env

            # ── Auto-generate LiteLLM key and store in Bitwarden ────────
            if command -v bw &>/dev/null; then
                LITELLM_KEY="sk-$(openssl rand -hex 24 2>/dev/null || head -c32 < /dev/urandom | xxd -p -c64)"
                litellm_item=$(bw list items --search "litellm-master-key" --organizationid "$BW_ORG_ID" --session "$BW_SESSION" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
for item in (data if isinstance(data, list) else []):
    if item.get('name') == 'litellm-master-key':
        print(item['id'])
" 2>/dev/null || true)
                if [[ -n "$litellm_item" ]]; then
                    info "Updating existing litellm-master-key in vault..."
                    bw get item "$litellm_item" --session "$BW_SESSION" 2>/dev/null | \
                        python3 -c "
import sys, json
item = json.load(sys.stdin)
item['login']['password'] = '${LITELLM_KEY}'
print(json.dumps(item))
" 2>/dev/null | \
                    bw encode | \
                    bw edit item "$litellm_item" --session "$BW_SESSION" >/dev/null 2>&1 || true
                else
                    info "Creating litellm-master-key in vault..."
                    item_json=$(printf '{"organizationId":"%s","name":"litellm-master-key","type":1,"login":{"username":"litellm","password":"%s","uris":[]}}' "$BW_ORG_ID" "$LITELLM_KEY")
                    echo "$item_json" | bw encode | bw create item --session "$BW_SESSION" >/dev/null 2>&1 || true
                fi
            fi

            # ── Attempt resolution ──────────────────────────────────────
            info "Attempting to resolve placeholders now..."
            if bash "${SCRIPT_DIR}/scripts/resolve-vaultwarden.sh"; then
                success "Placeholders resolved — secrets pulled from vault."
            else
                warn "Resolution incomplete. Create these items in your vault:"
                echo "  1. ${BW_ORG_ID}/anthropic-api-key  (login item, password = API key)"
                echo "  2. ${BW_ORG_ID}/gemini-api-key     (login item, password = API key)"
                echo ""
                echo "  litellm-master-key was auto-created with a generated key."
                echo "  Then run: ./scripts/resolve-vaultwarden.sh"
            fi
        else
            warn "No organization ID — skipping Bitwarden setup."
        fi
    fi
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
header "Installation Complete"

OLLA_PORT="${OLLA_PORT:-40114}"
RETRIEVER_PORT="${RETRIEVER_PORT:-42000}"

echo -e "${GREEN}${BOLD}Stack is running!${RESET}"
echo ""
echo -e "  Olla (router): ${BOLD}http://localhost:${OLLA_PORT}${RESET}"
echo -e "  Retriever:     ${BOLD}http://localhost:${RETRIEVER_PORT}/health${RESET}"
echo -e "  Ollama API:    ${BOLD}http://localhost:${OLLAMA_PORT:-11434}${RESET}"
echo -e "  LiteLLM UI:    ${BOLD}http://localhost:${LITELLM_PORT:-4000}/ui${RESET}"
echo ""
echo -e "${YELLOW}Next steps:${RESET}"
echo ""
echo -e "  ${BOLD}Obsidian setup:${RESET}"
echo -e "    1. Open Obsidian"
echo -e "    2. Click 'Open folder as vault' (or 'Manage vaults' → 'Open')"
echo -e "    3. Select this project folder: ${BOLD}${SCRIPT_DIR}${RESET}"
echo -e "    4. Go to Settings → Community Plugins → enable ${BOLD}OpenCode${RESET}"
echo -e "    5. Click the terminal icon in the sidebar (or Ctrl+Shift+O)"
echo ""
echo -e "  ${BOLD}RAG / vault search:${RESET}"
echo -e "    The retriever indexes notes at: ${BOLD}RETRIEVER_VAULT_PATH${RESET}"
echo -e "    Currently configured as: ${BOLD}${RETRIEVER_VAULT_PATH:-/home/${STACK_USER}/obsidian}${RESET}"
echo -e "    If your notes live elsewhere, update RETRIEVER_VAULT_PATH in .env"
echo -e "    Then restart the stack and use OpenCode to search your vault."
echo ""
echo -e "  ${BOLD}Need help?${RESET}  docs/retriever-guide.md  |  docs/troubleshooting.md"

