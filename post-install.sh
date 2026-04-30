#!/usr/bin/env bash
# ─── post-install.sh ──────────────────────────────────────────────────────────
# Configures Open WebUI, LiteLLM, and Khoj after the stack comes up.
# Uses OLLAMA_REMOTE_* entries in .env as the single source of truth for
# all Ollama instances — no node addresses are baked into this script.
#
# What this does (in order):
#   1.  Generate tools/system_diagnostics.py with current instance list
#   2.  Wait for Open WebUI to become healthy
#   3.  Create or sign in to the admin account
#   4.  Register Ollama connections (local arc + all OLLAMA_REMOTE_* nodes)
#   5.  Register Pipelines connection
#   6.  Register LiteLLM as an OpenAI-compatible connection (Claude, Gemini)
#   7.  Register Open Terminal
#   8.  Deploy System Diagnostics tool to Open WebUI
#   9.  Enable System Diagnostics tool on all models
#   10. Verify Khoj health and print Obsidian plugin setup instructions
#   11. Configure Khoj chat models via Django shell
#
# Usage:
#   ./post-install.sh              # apply all configuration
#   ./post-install.sh --dry-run    # preview changes without applying
#
# Verified API prefixes (Open WebUI v0.9.x):
#   /api/v1/auths       — signup/signin
#   /api/v1/configs     — connections, ollama, openai
#   /api/v1/tools       — tool management
#   /api/v1/models      — model management
#   /api/v1/terminals   — terminal integration
#   /api/v1/pipelines   — pipeline management

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
[[ -f "${SCRIPT_DIR}/.env" ]] || error ".env not found. Copy .env.example first."
set -a
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.env"
set +a

WEBUI_PORT="${WEBUI_PORT:-3000}"
WEBUI_URL="http://localhost:${WEBUI_PORT}"
KHOJ_PORT="${KHOJ_PORT:-42110}"
KHOJ_URL="http://localhost:${KHOJ_PORT}"
LITELLM_PORT="${LITELLM_PORT:-4000}"
LITELLM_URL="http://localhost:${LITELLM_PORT}"
LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-local-admin}"
PIPELINES_API_KEY="${PIPELINES_API_KEY:-changeme}"
OPEN_TERMINAL_API_KEY="${OPEN_TERMINAL_API_KEY:-changeme}"

# ─── Collect OLLAMA_REMOTE_* entries from .env ────────────────────────────────
# Same parsing logic as generate-olla-config.sh — single source of truth.
declare -A REMOTE_INSTANCES   # name → url
declare -A REMOTE_PRIORITIES  # name → priority (default 70)

while IFS='=' read -r key val; do
  [[ "$key" =~ ^OLLAMA_REMOTE_([A-Za-z0-9_]+)$ ]] || continue
  name="${BASH_REMATCH[1]}"
  val="${val%%#*}"; val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"
  [[ -n "$val" ]] || continue
  REMOTE_INSTANCES["$name"]="$val"
  priority_var="OLLAMA_REMOTE_${name}_PRIORITY"
  REMOTE_PRIORITIES["$name"]="${!priority_var:-70}"
done < "${SCRIPT_DIR}/.env"

info "Instances from .env:"
info "  local (arc) → http://ollama-arc:11434  [priority 100]"
for name in "${!REMOTE_INSTANCES[@]}"; do
  info "  ${name,,} → ${REMOTE_INSTANCES[$name]}  [priority ${REMOTE_PRIORITIES[$name]}]"
done
info "  litellm-cloud → http://litellm:4000  [priority 50]"

# ─── API helper ───────────────────────────────────────────────────────────────
api() {
  local method="$1" endpoint="$2" data="${3:-}"
  local args=(-sf -X "${method}" "${WEBUI_URL}${endpoint}"
    -H "Content-Type: application/json")
  [[ -n "${TOKEN:-}" ]] && args+=(-H "Authorization: Bearer ${TOKEN}")
  [[ -n "$data" ]] && args+=(-d "$data")
  curl "${args[@]}" 2>/dev/null || echo ""
}

# ─── Step 1: Generate system_diagnostics.py ───────────────────────────────────
header "Step 1: Generate System Diagnostics Tool"

TOOL_FILE="${SCRIPT_DIR}/tools/system_diagnostics.py"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would regenerate ${TOOL_FILE} with OLLAMA_INSTANCES from .env:"
  would "local → http://ollama-arc:11434"
  for name in "${!REMOTE_INSTANCES[@]}"; do
    would "${name,,} → ${REMOTE_INSTANCES[$name]}"
  done
else
  [[ -f "$TOOL_FILE" ]] || error "tools/system_diagnostics.py not found. Expected at: ${TOOL_FILE}"

  # Build python assignment statements for remote instances
  REMOTE_ASSIGNMENTS=""
  for name in "${!REMOTE_INSTANCES[@]}"; do
    REMOTE_ASSIGNMENTS+="instances['${name,,}'] = '${REMOTE_INSTANCES[$name]}'"$'\n'
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

print(f"Written with {len(instances)} instance(s): {', '.join(instances.keys())}")
PYEOF
  success "Generated tools/system_diagnostics.py"
  info "Tip: commit this file — it's safe to check in (no IPs, just logical names)."
fi

# ─── Step 2: Wait for Open WebUI ─────────────────────────────────────────────
header "Step 2: Open WebUI"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would wait for Open WebUI at ${WEBUI_URL}/health"
else
  info "Waiting for Open WebUI at ${WEBUI_URL}..."
  waited=0; max_wait=120
  while (( waited < max_wait )); do
    if curl -sf "${WEBUI_URL}/health" 2>/dev/null | grep -q "true"; then
      success "Open WebUI is ready."
      break
    fi
    sleep 3; (( waited += 3 )) || true
  done
  (( waited >= max_wait )) && error "Open WebUI did not become ready. Check: docker logs open-webui"
fi

# ─── Step 3: Admin account ────────────────────────────────────────────────────
header "Step 3: Admin Account"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would check /api/v1/auths/admin/config for existing admin account"
  would "Fresh install: create account (prompt for email / password / name)"
  would "Existing account: sign in (prompt for credentials)"
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

# ─── Step 4: Ollama connections ───────────────────────────────────────────────
header "Step 4: Ollama Connections"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would configure Ollama connections via /ollama/config/update:"
  would "local → http://ollama-arc:11434  [connection_type: local]"
  for name in "${!REMOTE_INSTANCES[@]}"; do
    would "${name,,} → ${REMOTE_INSTANCES[$name]}  [connection_type: external]"
  done
  warn "Existing connections not in .env will be removed (rebuilt from scratch each run)."
else
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

  RESULT=$(curl -sf -X POST "${WEBUI_URL}/ollama/config/update" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"ENABLE_OLLAMA_API\":true,\"OLLAMA_BASE_URLS\":${OLLAMA_URLS},\"OLLAMA_API_CONFIGS\":${OLLAMA_API_CFGS}}" \
    2>/dev/null || echo "")

  if echo "$RESULT" | grep -q "ollama-arc"; then
    success "Ollama connections configured (1 local + ${#REMOTE_INSTANCES[@]} remote)."
  else
    warn "Could not update Ollama connections — set manually in Admin Panel → Connections."
  fi
fi

# ─── Step 5: Pipelines connection ─────────────────────────────────────────────
header "Step 5: Pipelines"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would add Pipelines to OpenAI connections if not already present:"
  would "http://pipelines:9099  key: PIPELINES_API_KEY"
else
  OPENAI_CFG=$(curl -sf "${WEBUI_URL}/openai/config" \
    -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "{}")

  RESULT=$(echo "$OPENAI_CFG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
urls = cfg.get('OPENAI_API_BASE_URLS', [])
keys = cfg.get('OPENAI_API_KEYS', [])
api_cfgs = cfg.get('OPENAI_API_CONFIGS', {})
url  = 'http://pipelines:9099'
key  = '${PIPELINES_API_KEY}'
if url not in urls:
    idx = str(len(urls))
    urls.append(url); keys.append(key)
    api_cfgs[idx] = {'enable': True, 'tags': [], 'prefix_id': '',
                     'model_ids': [], 'connection_type': 'external', 'auth_type': 'bearer'}
cfg.update({'OPENAI_API_BASE_URLS': urls, 'OPENAI_API_KEYS': keys, 'OPENAI_API_CONFIGS': api_cfgs})
print(json.dumps(cfg))
" | curl -sf -X POST "${WEBUI_URL}/openai/config/update" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d @- 2>/dev/null || echo "")

  if echo "$RESULT" | grep -q "pipelines"; then
    success "Pipelines connection configured."
  else
    warn "Could not configure Pipelines — set manually in Admin Panel → Connections."
  fi
fi

# ─── Step 6: LiteLLM connection (Claude, Gemini) ─────────────────────────────
header "Step 6: LiteLLM Cloud Connection"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would add LiteLLM to OpenAI connections if not already present:"
  would "http://litellm:4000/v1  key: LITELLM_MASTER_KEY"
  would "Exposes Claude, Gemini models directly in Open WebUI model picker."
else
  OPENAI_CFG=$(curl -sf "${WEBUI_URL}/openai/config" \
    -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "{}")

  RESULT=$(echo "$OPENAI_CFG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
urls = cfg.get('OPENAI_API_BASE_URLS', [])
keys = cfg.get('OPENAI_API_KEYS', [])
api_cfgs = cfg.get('OPENAI_API_CONFIGS', {})
url  = 'http://litellm:4000/v1'
key  = '${LITELLM_MASTER_KEY}'
if url not in urls:
    idx = str(len(urls))
    urls.append(url); keys.append(key)
    api_cfgs[idx] = {'enable': True, 'tags': [], 'prefix_id': 'cloud',
                     'model_ids': [], 'connection_type': 'external', 'auth_type': 'bearer'}
cfg.update({'OPENAI_API_BASE_URLS': urls, 'OPENAI_API_KEYS': keys, 'OPENAI_API_CONFIGS': api_cfgs})
print(json.dumps(cfg))
" | curl -sf -X POST "${WEBUI_URL}/openai/config/update" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d @- 2>/dev/null || echo "")

  if echo "$RESULT" | grep -q "litellm"; then
    success "LiteLLM connection configured — Claude and Gemini models now available."
  else
    warn "Could not configure LiteLLM — set manually in Admin Panel → Connections."
    warn "  URL: http://litellm:4000/v1    Key: ${LITELLM_MASTER_KEY}"
  fi
fi

# ─── Step 7: Open Terminal ────────────────────────────────────────────────────
header "Step 7: Open Terminal"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would check /api/v1/terminals/ for existing Open Terminal entry:"
  would "If not present: add http://open-terminal:8000"
  would "If already present: skip (idempotent)."
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

# ─── Step 8: Deploy System Diagnostics tool ───────────────────────────────────
header "Step 8: Deploy System Diagnostics Tool"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would deploy tools/system_diagnostics.py to Open WebUI:"
  would "If tool exists (id: system_diagnostics): update content"
  would "If tool missing: create new"
else
  if [[ ! -f "$TOOL_FILE" ]]; then
    warn "tools/system_diagnostics.py not found — skipping tool deployment."
  else
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
        success "System Diagnostics tool updated."
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
fi

# ─── Step 9: Enable tools on models ───────────────────────────────────────────
header "Step 9: Enable Tools on Models"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would fetch model list from /api/v1/models:"
  would "For each model without system_diagnostics in toolIds: add it"
  would "Models already configured and embed/pipeline models: skip"
else
  CUSTOM_MODELS=$(curl -sf "${WEBUI_URL}/api/v1/models" \
    -H "Authorization: Bearer ${TOKEN}" 2>/dev/null || echo "[]")

  MODEL_IDS=$(echo "$CUSTOM_MODELS" | python3 -c "
import sys, json
models = json.load(sys.stdin)
if isinstance(models, dict): models = models.get('data', [])
for m in models:
  mid = m.get('id','')
  existing = m.get('meta', {}).get('toolIds', [])
  skip = any(s in mid for s in ['embed','smart-router','pipeline'])
  if 'system_diagnostics' not in existing and not skip:
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
if 'system_diagnostics' not in tools: tools.append('system_diagnostics')
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

# ─── Step 10: Verify Khoj ─────────────────────────────────────────────────────
header "Step 10: Khoj"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would wait for Khoj at ${KHOJ_URL}/api/health"
  would "On success: attempt to retrieve API key and print Obsidian plugin instructions"
  would "On timeout: print troubleshooting steps"
else
  info "Waiting for Khoj at ${KHOJ_URL}..."
  khoj_ready=false
  for i in {1..15}; do
    if curl -sf "${KHOJ_URL}/api/health" 2>/dev/null | grep -qi "ok"; then
      khoj_ready=true; break
    fi
    sleep 4
  done

  if [[ "$khoj_ready" == "true" ]]; then
    success "Khoj is healthy."

    KHOJ_API_KEY=$(curl -sf -X POST "${KHOJ_URL}/auth/token" \
      -H "Content-Type: application/json" \
      -d "{\"username\":\"${KHOJ_ADMIN_EMAIL:-admin@localhost}\",\"password\":\"${KHOJ_ADMIN_PASSWORD:-changeme}\"}" \
      2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || echo "")

    echo ""
    echo -e "${GREEN}${BOLD}Khoj is ready!${RESET}"
    echo ""
    echo -e "${YELLOW}Obsidian plugin setup:${RESET}"
    echo "  1. Obsidian → Settings → Community Plugins → Browse → search 'Khoj' → Install"
    echo "  2. In Khoj plugin settings:"
    echo -e "       Server URL: ${BOLD}${KHOJ_URL}${RESET}"
    if [[ -n "$KHOJ_API_KEY" ]]; then
      echo -e "       API Key:    ${BOLD}${KHOJ_API_KEY}${RESET}"
    else
      echo "       API Key:    get from ${KHOJ_URL}/settings (admin credentials)"
    fi
    echo "  3. Click 'Force Sync' to index your vault immediately"
    echo ""
    echo -e "  Full guide: ${BOLD}docs/khoj-setup.md${RESET}"
  else
    warn "Khoj did not become ready within 60s."
    warn "  Check: docker logs khoj"
    warn "  Khoj requires nomic-embed-text — ensure the model is pulled in Ollama."
    warn "  Manual setup: see docs/khoj-setup.md"
  fi
fi

# ─── Step 11: Configure Khoj chat models ──────────────────────────────────────
header "Step 11: Khoj Chat Models"

if [[ "$DRY_RUN" == "true" ]]; then
  dry "Would configure Khoj chat models via Django shell inside khoj container:"
  would "Create AiModelApi entry: ollama → http://ollama-arc:11434/v1/"
  would "Register: gemma3:12b, qwen2.5:14b, qwen2.5-coder:14b, deepseek-r1:14b"
  would "Existing entries skipped (get_or_create — idempotent)."
else
  if ! curl -sf "${KHOJ_URL}/api/health" 2>/dev/null | grep -qi "ok"; then
    warn "Khoj not reachable — skipping model setup."
    warn "Re-run post-install.sh once Khoj is healthy, or configure at ${KHOJ_URL}/server/admin"
  else
    docker exec khoj bash -c 'cat > /tmp/setup_models.py << '"'"'PYEOF'"'"'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "khoj.app.settings")
import django
django.setup()
from khoj.database.models import ChatModel, AiModelApi

api, created = AiModelApi.objects.get_or_create(
    name="ollama",
    defaults={"api_key": "ollama", "api_base_url": "http://ollama-arc:11434/v1/"}
)
print("AiModelApi:", "created" if created else "exists", api.name)

for name, friendly, strengths in [
    ("gemma3:12b",        "Gemma 3 12B",        "Long context, logs, summaries"),
    ("qwen2.5:14b",       "Qwen 2.5 14B",       "Tool calling, diagnostics"),
    ("qwen2.5-coder:14b", "Qwen 2.5 Coder 14B", "Code, configs, scripting"),
    ("deepseek-r1:14b",   "DeepSeek R1 14B",    "Complex reasoning"),
]:
    obj, c = ChatModel.objects.get_or_create(
        name=name,
        defaults={
            "friendly_name": friendly,
            "model_type": ChatModel.ModelType.OPENAI,
            "ai_model_api": api,
            "strengths": strengths,
            "max_prompt_size": 8192,
        }
    )
    print("ChatModel:", "created" if c else "exists", obj.name)
print("Done!")
PYEOF'

    RESULT=$(docker exec khoj python3 /tmp/setup_models.py 2>&1)
    docker exec khoj rm -f /tmp/setup_models.py

    if echo "$RESULT" | grep -q "Done!"; then
      echo "$RESULT" | while IFS= read -r line; do info "  $line"; done
      success "Khoj chat models configured."
    else
      warn "Khoj model setup may have failed:"
      echo "$RESULT"
      warn "Configure manually at ${KHOJ_URL}/server/admin"
    fi
  fi
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
header "Summary"

if [[ "$DRY_RUN" == "true" ]]; then
  echo -e "${CYAN}${BOLD}Dry-run complete — no changes were made.${RESET}"
  echo ""
  echo -e "${CYAN}Planned instance registrations:${RESET}"
  echo "  local (arc) → http://ollama-arc:11434"
  for name in "${!REMOTE_INSTANCES[@]}"; do
    echo "  ${name,,} → ${REMOTE_INSTANCES[$name]}"
  done
  echo "  litellm-cloud → http://litellm:4000/v1  (Claude, Gemini)"
  echo ""
  echo -e "Run ${BOLD}./post-install.sh${RESET} to apply."
else
  echo -e "${GREEN}${BOLD}Configuration applied!${RESET}"
  echo ""
  echo -e "  Open WebUI:  ${BOLD}${WEBUI_URL}${RESET}"
  echo -e "  LiteLLM UI:  ${BOLD}${LITELLM_URL}/ui${RESET}  (admin / ${LITELLM_MASTER_KEY})"
  echo -e "  Khoj:        ${BOLD}${KHOJ_URL}${RESET}"
  echo ""
  echo -e "${YELLOW}Registered instances:${RESET}"
  echo "  local (arc) → http://ollama-arc:11434"
  for name in "${!REMOTE_INSTANCES[@]}"; do
    echo "  ${name,,} → ${REMOTE_INSTANCES[$name]}"
  done
  echo "  litellm-cloud → http://litellm:4000/v1  (Claude, Gemini)"
  echo ""
  echo -e "${YELLOW}tools/system_diagnostics.py was regenerated — safe to commit:${RESET}"
  echo -e "  ${BOLD}git add tools/ && git commit -m 'update instances'${RESET}"
  echo ""
  echo -e "${YELLOW}Any [WARN] steps need manual completion — see:${RESET}"
  echo "  docs/post-install.md and docs/khoj-setup.md"
fi
