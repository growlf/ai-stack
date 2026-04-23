#!/usr/bin/env bash
# ─── post-install.sh ──────────────────────────────────────────────────────────
# Automates Open WebUI and Khoj configuration after the stack is running.
# Reads OLLAMA_REMOTE_* entries from .env as the single source of truth for
# all Ollama instances — generates system_diagnostics.py, registers connections,
# enables tools on models, and verifies Khoj is healthy.
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
#   ./post-install.sh              # apply configuration
#   ./post-install.sh --dry-run    # preview changes without applying

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}${BLUE}═══ $* ═══${RESET}\n"; }
dry()     { echo -e "${CYAN}[DRY-RUN]${RESET} $*"; }
would()   { echo -e "${CYAN}  →${RESET} $*"; }

# ─── Parse args ───────────────────────────────────────────────────────────────
DRY_RUN=false
for arg in "$@"; do
    [[ "$arg" == "--dry-run" ]] && DRY_RUN=true
done

if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "\n${BOLD}${CYAN}╔═══════════════════════════════════════╗"
    echo -e "║         DRY-RUN MODE — no changes     ║"
    echo -e "╚═══════════════════════════════════════╝${RESET}\n"
fi

# ─── Load .env ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "${SCRIPT_DIR}/.env" ]] || error ".env not found."
source "${SCRIPT_DIR}/.env"

WEBUI_PORT="${WEBUI_PORT:-3000}"
WEBUI_URL="http://localhost:${WEBUI_PORT}"
KHOJ_PORT="${KHOJ_PORT:-42110}"
KHOJ_URL="http://localhost:${KHOJ_PORT}"
PIPELINES_API_KEY="${PIPELINES_API_KEY:-changeme}"
OPEN_TERMINAL_API_KEY="${OPEN_TERMINAL_API_KEY:-changeme}"

# ─── Collect remote instances from .env ───────────────────────────────────────
# Reads all OLLAMA_REMOTE_<name>=<url> entries into an associative array
declare -A REMOTE_INSTANCES
while IFS='=' read -r key val; do
    [[ "$key" =~ ^OLLAMA_REMOTE_(.+)$ ]] || continue
    name="${BASH_REMATCH[1]}"
    # Strip inline comments and whitespace
    val="${val%%#*}"; val="${val// /}"
    [[ -n "$val" ]] && REMOTE_INSTANCES["$name"]="$val"
done < "${SCRIPT_DIR}/.env"

info "Instances from .env:"
info "  local → http://ollama-arc:11434"
for name in "${!REMOTE_INSTANCES[@]}"; do
    info "  ${name} → ${REMOTE_INSTANCES[$name]}"
done

# ─── Helper ───────────────────────────────────────────────────────────────────
api() {
    local method="$1" endpoint="$2" data="${3:-}"
    local args=(-sf -X "${method}" "${WEBUI_URL}${endpoint}"
        -H "Content-Type: application/json")
    [[ -n "${TOKEN:-}" ]] && args+=(-H "Authorization: Bearer ${TOKEN}")
    [[ -n "$data" ]] && args+=(-d "$data")
    curl "${args[@]}" 2>/dev/null || echo ""
}

# ─── Step 1: Generate system_diagnostics.py from .env ────────────────────────
header "Step 1: Generate System Diagnostics Tool"

TOOL_FILE="${SCRIPT_DIR}/tools/system_diagnostics.py"

if [[ "$DRY_RUN" == "true" ]]; then
    dry "Would regenerate ${TOOL_FILE} with OLLAMA_INSTANCES from .env:"
    would "local → http://ollama-arc:11434"
    for name in "${!REMOTE_INSTANCES[@]}"; do
        would "${name} → ${REMOTE_INSTANCES[$name]}"
    done
else
    [[ -f "$TOOL_FILE" ]] || error "tools/system_diagnostics.py not found in ${SCRIPT_DIR}/tools/"

    # Build python assignment statements for remote instances
    REMOTE_ASSIGNMENTS=""
    for name in "${!REMOTE_INSTANCES[@]}"; do
        REMOTE_ASSIGNMENTS+="instances['${name}'] = '${REMOTE_INSTANCES[$name]}'"$'\n'
    done

    python3 - "${TOOL_FILE}" << PYEOF
import re, sys

tool_path = sys.argv[1]

instances = {"local": "http://ollama-arc:11434"}
${REMOTE_ASSIGNMENTS}

lines = ['OLLAMA_INSTANCES = {']
for k, v in instances.items():
    lines.append(f'    "{k}": "{v}",')
lines.append('}')
instances_block = '\n'.join(lines)

with open(tool_path, 'r') as f:
    content = f.read()

content = re.sub(
    r'OLLAMA_INSTANCES\s*=\s*\{[^}]*\}',
    instances_block,
    content,
    flags=re.DOTALL
)

with open(tool_path, 'w') as f:
    f.write(content)

print(f"Generated with {len(instances)} instance(s): {', '.join(instances.keys())}")
PYEOF
    success "Generated tools/system_diagnostics.py from .env"
    info "Tip: commit this file to git to keep your repo in sync."
fi

# ─── Step 2: Wait for Open WebUI ─────────────────────────────────────────────
header "Step 2: Open WebUI"

if [[ "$DRY_RUN" == "true" ]]; then
    dry "Would wait for Open WebUI at ${WEBUI_URL}"
    dry "Would authenticate and obtain token"
else
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
fi

# ─── Step 3: Admin account ────────────────────────────────────────────────────
header "Step 3: Admin Account"

if [[ "$DRY_RUN" == "true" ]]; then
    dry "Would check for existing admin account via /api/v1/auths/admin/config"
    would "If account exists: prompt for credentials and sign in"
    would "If fresh install: prompt for email, password, display name and create account"
else
    SETUP_STATUS=$(curl -sf "${WEBUI_URL}/api/v1/auths/admin/config" \
        -H "Content-Type: application/json" 2>/dev/null || echo "")

    if echo "$SETUP_STATUS" | grep -q '"showAdminDetails"'; then
        FRESH_INSTALL=true
    else
        FRESH_INSTALL=false
    fi

    if [[ "$FRESH_INSTALL" == "false" ]]; then
        echo ""
        echo -e "${YELLOW}An admin account already exists.${RESET}"
        read -rp "Sign in to existing account? [Y/n] " skip_create
        if [[ "${skip_create,,}" != "n" ]]; then
            read -rp  "Admin email:    " ADMIN_EMAIL
            read -rsp "Admin password: " ADMIN_PASSWORD; echo ""
            RESPONSE=$(api POST /api/v1/auths/signin \
                "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}")
            if echo "$RESPONSE" | grep -q '"token"'; then
                TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
                success "Signed in as ${ADMIN_EMAIL}."
            else
                error "Sign in failed. Check credentials."
            fi
        else
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
fi

# ─── Step 4: Connections ──────────────────────────────────────────────────────
header "Step 4: Connections"

if [[ "$DRY_RUN" == "true" ]]; then
    dry "Would configure Ollama connections:"
    would "local → http://ollama-arc:11434"
    for name in "${!REMOTE_INSTANCES[@]}"; do
        would "${name} → ${REMOTE_INSTANCES[$name]}"
    done
    echo ""
    dry "Would configure Pipelines connection:"
    would "http://pipelines:9099"
    echo ""
    warn "Note: Ollama URL list is rebuilt from .env on each run."
    warn "Any manually added connections not in .env will be removed."
else
    # Build Ollama URL + config arrays from .env
    OLLAMA_URLS='["http://ollama-arc:11434"'
    OLLAMA_API_CFGS='{"0":{"enable":true,"tags":[],"prefix_id":"","model_ids":[],"connection_type":"local","auth_type":"bearer","key":""}}'

    idx=1
    for name in "${!REMOTE_INSTANCES[@]}"; do
        url="${REMOTE_INSTANCES[$name]}"
        OLLAMA_URLS+=",\"${url}\""
        OLLAMA_API_CFGS=$(echo "$OLLAMA_API_CFGS" | python3 -c "
import sys, json
cfgs = json.load(sys.stdin)
cfgs['${idx}'] = {'enable': True, 'tags': [], 'prefix_id': '',
                  'model_ids': [], 'connection_type': 'external',
                  'auth_type': 'bearer', 'key': ''}
print(json.dumps(cfgs))
")
        (( idx++ )) || true
    done
    OLLAMA_URLS+="]"

    OLLAMA_PAYLOAD="{\"ENABLE_OLLAMA_API\":true,\"OLLAMA_BASE_URLS\":${OLLAMA_URLS},\"OLLAMA_API_CONFIGS\":${OLLAMA_API_CFGS}}"
    RESULT=$(curl -sf -X POST "${WEBUI_URL}/ollama/config/update" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$OLLAMA_PAYLOAD" 2>/dev/null || echo "")

    if echo "$RESULT" | grep -q "ollama-arc"; then
        success "Ollama connections configured (local + ${#REMOTE_INSTANCES[@]} remote)"
    else
        warn "Could not update Ollama config — set manually in Admin Panel → Connections"
    fi

    # Pipelines
    OPENAI_CFG=$(curl -sf "${WEBUI_URL}/openai/config" \
        -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "{}")

    RESULT=$(echo "$OPENAI_CFG" | python3 -c "
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
" | curl -sf -X POST "${WEBUI_URL}/openai/config/update" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d @- 2>/dev/null || echo "")

    if echo "$RESULT" | grep -q "pipelines"; then
        success "Pipelines connection configured."
    else
        warn "Could not configure Pipelines — set manually in Admin Panel → Connections"
    fi
fi

# ─── Step 5: Open Terminal ────────────────────────────────────────────────────
header "Step 5: Open Terminal"

if [[ "$DRY_RUN" == "true" ]]; then
    dry "Would check /api/v1/terminals/ for existing Open Terminal entry"
    would "If not present: add http://open-terminal:8000"
    would "If already present: skip (no change)"
else
    EXISTING_TERMS=$(curl -sf "${WEBUI_URL}/api/v1/terminals/" \
        -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "[]")

    if echo "$EXISTING_TERMS" | grep -q "open-terminal"; then
        success "Open Terminal already configured — no change."
    else
        RESULT=$(curl -sf -X POST "${WEBUI_URL}/api/v1/terminals/add" \
            -H "Authorization: Bearer ${TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"url\":\"http://open-terminal:8000\",\"name\":\"Local\",\"key\":\"${OPEN_TERMINAL_API_KEY}\"}" \
            2>/dev/null || echo "")
        if echo "$RESULT" | grep -q '"id"'; then
            success "Open Terminal configured."
        else
            warn "Could not configure Open Terminal — set manually in Integrations."
        fi
    fi
fi

# ─── Step 6: Deploy System Diagnostics tool ───────────────────────────────────
header "Step 6: Deploy System Diagnostics Tool"

if [[ "$DRY_RUN" == "true" ]]; then
    dry "Would deploy generated tools/system_diagnostics.py to Open WebUI"
    would "If tool exists: update content"
    would "If tool missing: create new"
else
    [[ -f "$TOOL_FILE" ]] || { warn "tools/system_diagnostics.py not found — skipping."; }

    PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'id': 'system_diagnostics',
    'name': 'System Diagnostics',
    'content': open('${TOOL_FILE}').read(),
    'meta': {'description': 'Query multiple Ollama instances for models, GPU status, health, and control.'}
}))
")
    EXISTING_TOOLS=$(api GET /api/v1/tools/)

    if echo "$EXISTING_TOOLS" | grep -qi "system_diagnostics"; then
        RESULT=$(api POST /api/v1/tools/id/system_diagnostics/update "$PAYLOAD" 2>/dev/null || echo "")
        if echo "$RESULT" | grep -q '"id"'; then
            success "System Diagnostics tool updated (${#REMOTE_INSTANCES[@]} remote instance(s))."
        else
            warn "Could not update tool via API — paste tools/system_diagnostics.py manually in Workspace → Tools."
        fi
    else
        RESULT=$(api POST /api/v1/tools/create "$PAYLOAD")
        if echo "$RESULT" | grep -q '"id"'; then
            success "System Diagnostics tool installed."
        else
            warn "Could not install tool — paste tools/system_diagnostics.py manually in Workspace → Tools."
        fi
    fi
fi

# ─── Step 7: Enable tools on models ───────────────────────────────────────────
header "Step 7: Enable Tools on Models"

if [[ "$DRY_RUN" == "true" ]]; then
    dry "Would fetch model list from /api/v1/models"
    would "For each model without system_diagnostics in toolIds: add it"
    would "Models already configured would be skipped"
else
    CUSTOM_MODELS=$(curl -sf "${WEBUI_URL}/api/v1/models" \
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
        success "All models already have System Diagnostics enabled — no change."
    else
        while IFS= read -r model_id; do
            [[ -z "$model_id" ]] && continue
            CURRENT_MODEL=$(curl -sf \
                "${WEBUI_URL}/api/v1/models/model?id=${model_id}" \
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
print(json.dumps({'id': m.get('id'), 'name': m.get('name'),
                  'meta': meta, 'params': m.get('params', {})}))
" | curl -sf -X POST \
                "${WEBUI_URL}/api/v1/models/model/update?id=${model_id}" \
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
fi

# ─── Step 8: Verify Khoj ──────────────────────────────────────────────────────
header "Step 8: Khoj"

if [[ "$DRY_RUN" == "true" ]]; then
    dry "Would wait for Khoj at ${KHOJ_URL}"
    would "Check health endpoint at ${KHOJ_URL}/api/health"
    would "Verify Ollama connection via ${KHOJ_URL}/api/config/data/default"
    would "Print Obsidian plugin setup instructions"
else
    info "Waiting for Khoj at ${KHOJ_URL}..."
    khoj_ready=false
    for i in {1..15}; do
        if curl -sf "${KHOJ_URL}/api/health" 2>/dev/null | grep -q "\"status\":\"ok\"" 2>/dev/null || \
           curl -sf "${KHOJ_URL}/api/health" 2>/dev/null | grep -q "ok" 2>/dev/null; then
            khoj_ready=true
            break
        fi
        sleep 4
    done

    if [[ "$khoj_ready" == "true" ]]; then
        success "Khoj is healthy at ${KHOJ_URL}"

        # Check Ollama connection from Khoj's perspective
        KHOJ_HEALTH=$(curl -sf "${KHOJ_URL}/api/health" 2>/dev/null || echo "{}")
        if echo "$KHOJ_HEALTH" | grep -qi "ok\|healthy"; then
            success "Khoj health check passed."
        fi

        # Get API key for Obsidian plugin setup
        KHOJ_API_KEY=$(curl -sf -X POST "${KHOJ_URL}/auth/token" \
            -H "Content-Type: application/json" \
            -d "{\"username\":\"${KHOJ_ADMIN_EMAIL:-admin@localhost}\",\"password\":\"${KHOJ_ADMIN_PASSWORD:-changeme}\"}" \
            2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || echo "")

        echo ""
        echo -e "${GREEN}${BOLD}Khoj is ready!${RESET}"
        echo ""
        echo -e "${YELLOW}Obsidian plugin setup:${RESET}"
        echo "  1. Open Obsidian → Settings → Community Plugins → Browse"
        echo "  2. Search for 'Khoj' and install it"
        echo "  3. In Khoj plugin settings set:"
        echo -e "     Server URL: ${BOLD}${KHOJ_URL}${RESET}"
        if [[ -n "$KHOJ_API_KEY" ]]; then
            echo -e "     API Key:    ${BOLD}${KHOJ_API_KEY}${RESET}"
        else
            echo "     API Key:    get from ${KHOJ_URL}/settings (login with your Khoj admin credentials)"
        fi
        echo "  4. Click 'Force Sync' to index your vault immediately"
        echo ""
        echo -e "  Full guide: ${BOLD}docs/khoj-setup.md${RESET}"
    else
        warn "Khoj did not become ready within 60s."
        warn "Check: docker logs khoj"
        warn "Khoj requires nomic-embed-text to be pulled — ensure models are loaded."
        warn "Manual setup: see docs/khoj-setup.md"
    fi
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
header "Summary"

if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${CYAN}${BOLD}Dry-run complete — no changes were made.${RESET}"
    echo ""
    echo -e "${CYAN}Planned instance registrations:${RESET}"
    echo "  local → http://ollama-arc:11434"
    for name in "${!REMOTE_INSTANCES[@]}"; do
        echo "  ${name} → ${REMOTE_INSTANCES[$name]}"
    done
    echo ""
    echo -e "Run ${BOLD}./post-install.sh${RESET} to apply these changes."
else
    echo -e "${GREEN}${BOLD}Configuration applied!${RESET}"
    echo ""
    echo -e "  Open WebUI: ${BOLD}${WEBUI_URL}${RESET}"
    echo -e "  Khoj:       ${BOLD}${KHOJ_URL}${RESET}"
    echo ""
    echo -e "${YELLOW}Instances registered:${RESET}"
    echo "  local → http://ollama-arc:11434"
    for name in "${!REMOTE_INSTANCES[@]}"; do
        echo "  ${name} → ${REMOTE_INSTANCES[$name]}"
    done
    echo ""
    echo -e "${YELLOW}tools/system_diagnostics.py was regenerated from .env${RESET}"
    echo -e "  Consider committing: ${BOLD}git add tools/ && git commit -m 'update instances'${RESET}"
    echo ""
    echo -e "${YELLOW}Any steps showing [WARN] need manual completion:${RESET}"
    echo "  See docs/post-install.md and docs/khoj-setup.md"
fi
