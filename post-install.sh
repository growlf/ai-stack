#!/usr/bin/env bash
# ─── post-install.sh ──────────────────────────────────────────────────────────
# Automates Open WebUI configuration after the stack is running.
# Uses verified API endpoints from Open WebUI v0.9.x source.
#
# Verified prefixes from main.py:
#   /api/v1/auths       — signup/signin
#   /api/v1/configs     — connections, ollama, openai
#   /api/v1/tools       — tool management
#   /api/v1/models      — model management
#   /api/v1/terminals   — terminal integration
#   /api/v1/pipelines   — pipeline management
#
# Usage:
#   ./post-install.sh

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}${BLUE}═══ $* ═══${RESET}\n"; }

# ─── Load .env ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "${SCRIPT_DIR}/.env" ]] || error ".env not found."
source "${SCRIPT_DIR}/.env"

WEBUI_PORT="${WEBUI_PORT:-3000}"
WEBUI_URL="http://localhost:${WEBUI_PORT}"
PIPELINES_API_KEY="${PIPELINES_API_KEY:-changeme}"
OPEN_TERMINAL_API_KEY="${OPEN_TERMINAL_API_KEY:-changeme}"

# ─── Wait for Open WebUI ──────────────────────────────────────────────────────
header "Waiting for Open WebUI"

wait_for_webui() {
    local max_wait=60 waited=0
    info "Waiting for Open WebUI at ${WEBUI_URL}..."
    while (( waited < max_wait )); do
        if curl -sf "${WEBUI_URL}/health" | grep -q "true" 2>/dev/null; then
            success "Open WebUI is ready."
            return 0
        fi
        sleep 2; (( waited += 2 )) || true
    done
    error "Open WebUI did not become ready. Check: docker logs open-webui"
}

wait_for_webui

# ─── Helper ───────────────────────────────────────────────────────────────────
api() {
    local method="$1" endpoint="$2" data="${3:-}"
    local args=(-sf -X "${method}" "${WEBUI_URL}${endpoint}"
        -H "Content-Type: application/json")
    [[ -n "${TOKEN:-}" ]] && args+=(-H "Authorization: Bearer ${TOKEN}")
    [[ -n "$data" ]] && args+=(-d "$data")
    curl "${args[@]}" 2>/dev/null || echo ""
}

# ─── Step 1: Admin account ────────────────────────────────────────────────────
header "Admin Account"

# Check if any admin account exists by attempting an unauthenticated call
# that returns 403 (auth required) vs 200 (no auth set up yet)
SETUP_STATUS=$(curl -sf "${WEBUI_URL}/api/v1/auths/admin/config" \
    -H "Content-Type: application/json" 2>/dev/null || echo "")

if echo "$SETUP_STATUS" | grep -q '"showAdminDetails"'; then
    # Got a response without auth — fresh instance, no admin yet
    FRESH_INSTALL=true
else
    FRESH_INSTALL=false
fi

if [[ "$FRESH_INSTALL" == "false" ]]; then
    echo ""
    echo -e "${YELLOW}An admin account appears to already exist.${RESET}"
    read -rp "Skip account creation and sign in to existing account? [Y/n] " skip_create
    if [[ "${skip_create,,}" != "n" ]]; then
        read -rp  "Admin email:    " ADMIN_EMAIL
        read -rsp "Admin password: " ADMIN_PASSWORD; echo ""
        RESPONSE=$(api POST /api/v1/auths/signin \
            "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}")
        if echo "$RESPONSE" | grep -q '"token"'; then
            TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
            success "Signed in as ${ADMIN_EMAIL}."
        else
            error "Sign in failed. Check credentials and try again."
        fi
    else
        # They said no to skip — fall through to create
        FRESH_INSTALL=true
    fi
fi

if [[ "$FRESH_INSTALL" == "true" ]]; then
    read -rp  "Admin email:    " ADMIN_EMAIL
    read -rsp "Admin password: " ADMIN_PASSWORD; echo ""
    read -rp  "Display name:   " ADMIN_NAME
    RESPONSE=$(api POST /api/v1/auths/signup \
        "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\",\"name\":\"${ADMIN_NAME}\"}")
    if echo "$RESPONSE" | grep -q '"token"'; then
        TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
        success "Admin account created."
    else
        error "Could not create admin account. Response: ${RESPONSE:0:200}"
    fi
fi


# ─── Step 2: Connections ──────────────────────────────────────────────────────
header "Connections"

# GET current ollama config to preserve existing OLLAMA_API_CONFIGS
OLLAMA_CFG=$(curl -sf http://localhost:${WEBUI_PORT}/ollama/config \
    -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "{}")

OPENAI_CFG=$(curl -sf http://localhost:${WEBUI_PORT}/openai/config \
    -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "{}")

# Update Ollama config — preserve existing API configs, just set URL
OLLAMA_RESULT=$(echo "$OLLAMA_CFG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
cfg['OLLAMA_BASE_URLS'] = ['http://ollama-arc:11434']
cfg.setdefault('OLLAMA_API_CONFIGS', {'0': {
    'enable': True, 'tags': [], 'prefix_id': '',
    'model_ids': [], 'connection_type': 'local',
    'auth_type': 'bearer', 'key': ''
}})
print(json.dumps(cfg))
" | curl -sf -X POST http://localhost:${WEBUI_PORT}/ollama/config/update \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d @- 2>/dev/null || echo "")

if echo "$OLLAMA_RESULT" | grep -q "ollama-arc"; then
    success "Ollama connection verified: http://ollama-arc:11434"
else
    warn "Could not update Ollama config — set manually in Connections."
fi

# Update OpenAI/Pipelines config
OPENAI_RESULT=$(echo "$OPENAI_CFG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
urls = cfg.get('OPENAI_API_BASE_URLS', [])
keys = cfg.get('OPENAI_API_KEYS', [])
api_cfgs = cfg.get('OPENAI_API_CONFIGS', {})
pipelines_url = 'http://pipelines:9099'
pipelines_key = '${PIPELINES_API_KEY}'
if pipelines_url not in urls:
    idx = str(len(urls))
    urls.append(pipelines_url)
    keys.append(pipelines_key)
    api_cfgs[idx] = {'enable': True, 'tags': [], 'prefix_id': '',
                     'model_ids': [], 'connection_type': 'external',
                     'auth_type': 'bearer'}
cfg['OPENAI_API_BASE_URLS'] = urls
cfg['OPENAI_API_KEYS'] = keys
cfg['OPENAI_API_CONFIGS'] = api_cfgs
print(json.dumps(cfg))
" | curl -sf -X POST http://localhost:${WEBUI_PORT}/openai/config/update \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d @- 2>/dev/null || echo "")

if echo "$OPENAI_RESULT" | grep -q "pipelines"; then
    success "Pipelines connection verified: http://pipelines:9099"
else
    warn "Could not update Pipelines config — set manually in Connections."
fi


# ─── Step 3: Open Terminal ────────────────────────────────────────────────────
header "Open Terminal"

# Check if already configured
EXISTING_TERMS=$(curl -sf http://localhost:${WEBUI_PORT}/api/v1/terminals/ \
    -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "[]")

if echo "$EXISTING_TERMS" | grep -q "open-terminal"; then
    success "Open Terminal already configured: http://open-terminal:8000"
else
    RESULT=$(curl -sf -X POST http://localhost:${WEBUI_PORT}/api/v1/terminals/add \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"url\":\"http://open-terminal:8000\",\"name\":\"Local\",\"key\":\"${OPEN_TERMINAL_API_KEY}\"}" \
        2>/dev/null || echo "")
    if echo "$RESULT" | grep -q '"id"'; then
        success "Open Terminal configured."
    else
        warn "Could not configure Open Terminal — set manually in Integrations."
        warn "  URL: http://open-terminal:8000  Key: ${OPEN_TERMINAL_API_KEY}"
    fi
fi


# ─── Step 4: Install System Diagnostics tool ──────────────────────────────────
header "System Diagnostics Tool"

TOOL_FILE="${SCRIPT_DIR}/tools/system_diagnostics.py"

if [[ ! -f "$TOOL_FILE" ]]; then
    warn "tools/system_diagnostics.py not found — skipping."
else
    # Check if already installed
    EXISTING=$(api GET /api/v1/tools/)
    if echo "$EXISTING" | grep -qi "system_diagnostics"; then
        success "System Diagnostics tool already installed."
    else
        PAYLOAD=$(python3 -c "
import json
content = open('${TOOL_FILE}').read()
print(json.dumps({
    'id': 'system_diagnostics',
    'name': 'System Diagnostics',
    'content': content,
    'meta': {
        'description': 'Query multiple Ollama instances for models, GPU status, health, and control.'
    }
}))
")
        RESULT=$(api POST /api/v1/tools/create "$PAYLOAD")
        if echo "$RESULT" | grep -q '"id"'; then
            success "System Diagnostics tool installed."
        else
            warn "Could not install tool via API."
            warn "Paste tools/system_diagnostics.py manually: Admin Panel → Tools → +"
            info "Response: ${RESULT:0:200}"
        fi
    fi
fi


# ─── Step 5: Enable tools on models ───────────────────────────────────────────
header "Enabling Tools on Models"

# Get custom models from /api/v1/models (not /base — that's Ollama native models)
CUSTOM_MODELS=$(curl -sf http://localhost:${WEBUI_PORT}/api/v1/models \
    -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "[]")

MODEL_IDS=$(echo "$CUSTOM_MODELS" | python3 -c "
import sys, json
models = json.load(sys.stdin)
if isinstance(models, dict):
    models = models.get('data', [])
for m in models:
    mid = m.get('id','')
    existing_tools = m.get('meta', {}).get('toolIds', [])
    if 'system_diagnostics' not in existing_tools:
        if not any(s in mid for s in ['embed','smart-router','pipeline']):
            print(mid)
" 2>/dev/null || echo "")

if [[ -z "$MODEL_IDS" ]]; then
    success "All models already have System Diagnostics enabled."
else
    while IFS= read -r model_id; do
        [[ -z "$model_id" ]] && continue
        # GET current model config first to preserve existing settings
        CURRENT_MODEL=$(curl -sf \
            "http://localhost:${WEBUI_PORT}/api/v1/models/model?id=${model_id}" \
            -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "{}")
        RESULT=$(echo "$CURRENT_MODEL" | python3 -c "
import sys, json
m = json.load(sys.stdin)
meta = m.get('meta', {})
tools = meta.get('toolIds', [])
if 'system_diagnostics' not in tools:
    tools.append('system_diagnostics')
meta['toolIds'] = tools
m['meta'] = meta
# ModelForm requires id, name, meta, params
print(json.dumps({'id': m.get('id'), 'name': m.get('name'),
                  'meta': meta, 'params': m.get('params', {})}))
" | curl -sf -X POST \
            "http://localhost:${WEBUI_PORT}/api/v1/models/model/update?id=${model_id}" \
            -H "Authorization: Bearer ${TOKEN}" \
            -H "Content-Type: application/json" \
            -d @- 2>/dev/null || echo "")
        if echo "$RESULT" | grep -q '"id"'; then
            success "Enabled System Diagnostics on: ${model_id}"
        else
            warn "Could not update: ${model_id}"
        fi
    done <<< "$MODEL_IDS"
fi


# ─── Done ─────────────────────────────────────────────────────────────────────
header "Post-Install Complete"

echo -e "${GREEN}${BOLD}Configuration applied!${RESET}"
echo ""
echo -e "${YELLOW}Verify in Open WebUI at ${WEBUI_URL}:${RESET}"
echo "  Admin Panel → Settings → Connections   — Ollama + Pipelines green"
echo "  Admin Panel → Settings → Integrations  — Open Terminal enabled"
echo "  Admin Panel → Settings → Pipelines     — smart_model_router listed"
echo "  Admin Panel → Tools                    — System Diagnostics present"
echo "  New chat → tools icon                  — System Diagnostics toggleable"
echo ""
echo -e "${YELLOW}Any steps showing [WARN] above need manual completion:${RESET}"
echo "  See docs/post-install.md"
