#!/usr/bin/env bash
# sync-models.sh — Sync AI models across cluster nodes over LAN
#
# Detects which models each node is missing and syncs them.
# Sync strategies (tried in order):
#   1. SSH + rsync (zero internet, LAN speed) — if SSH keys are deployed
#   2. Ollama /api/pull via HTTP           — uses internet, fallback
#
# Usage:
#   ./sync-models.sh                                    # sync all nodes
#   ./sync-models.sh 10.10.0.212                        # sync one node
#   ./sync-models.sh --source 10.10.0.201                # use different source
#   ./sync-models.sh --ssh-key ~/.ssh/cluster_ed25519    # specify SSH key
#   ./sync-models.sh --dry-run                           # preview only
#
# Dependencies: curl, jq or python3, rsync (if using SSH)
#
# Environment:
#   OLLAMA_PORT      — default 11434
#   SSH_USER         — default netyeti
#   SYNC_SOURCE      — default localhost (this machine)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Config ──────────────────────────────────────────────────────────────────
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
SSH_USER="${SSH_USER:-netyeti}"
DRY_RUN=false
SSH_KEY=""
declare -a TARGETS=()
SOURCE="local"

# Color helpers
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e " ${GREEN}✓${NC} $1"; }
warn() { echo -e " ${YELLOW}⚠${NC} $1"; }
fail() { echo -e " ${RED}✗${NC} $1"; }
info() { echo -e " ${CYAN}→${NC} $1"; }
header() { echo -e "\n${BOLD}$1${NC}"; echo "──────────────────────────────"; }

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --help|-h) echo "Usage: $0 [--source HOST] [--ssh-key PATH] [--dry-run] [TARGET...]"; exit 0 ;;
    *) TARGETS+=("$1"); shift ;;
  esac
done

# ── Load remote nodes from .env if no targets specified ────────────────────
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  if [[ -f "${PROJECT_DIR}/.env" ]]; then
    while IFS='=' read -r key val; do
      [[ "$key" =~ ^OLLAMA_REMOTE_([A-Za-z0-9_]+)$ ]] || continue
      val="${val%%#*}"; val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"
      [[ -n "$val" ]] || continue
      # Strip priority suffix if present
      url="${val%:[0-9]}"
      host="${url#http://}"
      host="${host#https://}"
      host="${host%:*}"
      TARGETS+=("$host")
    done < "${PROJECT_DIR}/.env"
  fi
  if [[ ${#TARGETS[@]} -eq 0 ]]; then
    echo "No targets specified and no OLLAMA_REMOTE_* found in .env"
    echo "Usage: $0 [--source HOST] [--ssh-key PATH] [--dry-run] [TARGET...]"
    exit 1
  fi
fi

# Remove duplicates while preserving order
declare -A _seen=()
declare -a unique=()
for t in "${TARGETS[@]}"; do
  [[ -n "${_seen[$t]:-}" ]] && continue
  _seen[$t]=1; unique+=("$t")
done
TARGETS=("${unique[@]}")

# ── Helpers ─────────────────────────────────────────────────────────────────
list_models() {
  local host="$1"
  if [[ "$host" == "local" ]]; then
    curl -sf "http://localhost:${OLLAMA_PORT}/api/tags" 2>/dev/null
  else
    curl -sf --connect-timeout 5 "http://${host}:${OLLAMA_PORT}/api/tags" 2>/dev/null
  fi | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for m in d.get('models', []):
        name = m['name']
        digest = m.get('digest', '')
        size = m.get('size', 0)
        print(f'{name}\t{digest}\t{size}')
except: pass
" 2>/dev/null
}

model_names()  { cut -f1; }
model_digests(){ cut -f2; }

check_ssh() {
  local host="$1"
  local ssh_cmd="ssh"
  [[ -n "$SSH_KEY" ]] && ssh_cmd="ssh -i ${SSH_KEY}"
  ${ssh_cmd} -o ConnectTimeout=3 -o BatchMode=yes "${host}" "echo ok" 2>/dev/null | grep -q ok
}

rsync_models() {
  local src_host="$1" dst_host="$2"
  local ssh_cmd="ssh"
  [[ -n "$SSH_KEY" ]] && ssh_cmd="ssh -i ${SSH_KEY}"
  local ssh_opt="${ssh_cmd} -o StrictHostKeyChecking=accept-new"

  if [[ "$DRY_RUN" == "true" ]]; then
    info "[DRY-RUN] Would rsync ollama models from ${src_host} to ${dst_host}"
    return 0
  fi

  local src_path dst_path
  if [[ "$src_host" == "local" ]]; then
    src_path="${HOME}/.ollama/models/"
  else
    src_path="${src_host}:~/.ollama/models/"
  fi
  dst_path="${dst_host}:~/.ollama/models/"

  info "Rsyncing models from ${src_host} to ${dst_host}..."
  rsync -avz --progress -e "${ssh_opt}" \
    --include="blobs/" --include="manifests/" \
    "${src_path}" "${dst_path}" 2>&1 | tail -5
  ok "Rsync complete"
}

pull_via_api() {
  local host="$1" model="$2"
  if [[ "$DRY_RUN" == "true" ]]; then
    info "[DRY-RUN] Would pull ${model} on ${host}"
    return 0
  fi
  info "Pulling ${model} on ${host}..."
  local resp
  resp=$(curl -s -X POST "http://${host}:${OLLAMA_PORT}/api/pull" \
    -d "{\"name\":\"${model}\",\"stream\":false}" 2>&1)
  if echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='success' else 1)" 2>/dev/null; then
    ok "${model} pulled on ${host}"
  else
    local err
    err=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','unknown'))" 2>/dev/null || echo "$resp")
    fail "${model} on ${host}: ${err}"
  fi
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
  echo -e "${BOLD}AI Stack — Model Sync${NC}"
  echo "Source: ${SOURCE}  |  Targets: ${TARGETS[*]}"
  [[ "$DRY_RUN" == "true" ]] && echo -e " ${YELLOW}DRY RUN MODE${NC}\n"

  # Get source models
  header "Source node models"
  local src_models
  src_models=$(list_models "$SOURCE" || true)
  if [[ -z "$src_models" ]]; then
    fail "Cannot list models on source: ${SOURCE}"
    exit 1
  fi
  echo "$src_models" | cut -f1 | while IFS= read -r name; do
    printf "  %-35s\n" "$name"
  done
  local total_src
  total_src=$(echo "$src_models" | wc -l)
  ok "$total_src models on source"

  # Process each target
  for target in "${TARGETS[@]}"; do
    header "Target: ${target}"

    # Get target models
    local tgt_models
    tgt_models=$(list_models "$target" || true)
    if [[ -z "$tgt_models" ]]; then
      fail "Cannot reach ${target}:${OLLAMA_PORT}"
      # Show what we'd sync
      local missing
      missing=$(echo "$src_models" | model_names)
      warn "$(echo "$missing" | wc -l) models would be synced (unreachable)"
      continue
    fi
    ok "$(echo "$tgt_models" | wc -l) models present"

    # Find missing models
    local tgt_names
    tgt_names=$(echo "$tgt_models" | model_names | sort)
    local missing_models
    missing_models=$(comm -23 <(echo "$src_models" | model_names | sort) <(echo "$tgt_names"))

    if [[ -z "$missing_models" ]]; then
      ok "All source models already present"
      continue
    fi

    local count
    count=$(echo "$missing_models" | wc -l)
    warn "${count} model(s) missing:"
    echo "$missing_models" | sed 's/^/     /'

    # Try SSH + rsync first
    if check_ssh "$target"; then
      info "SSH access available — using rsync over LAN"
      rsync_models "$SOURCE" "$target"
    else
      info "No SSH access — falling back to HTTP pull (uses internet)"
      echo "$missing_models" | while IFS= read -r model; do
        [[ -z "$model" ]] && continue
        pull_via_api "$target" "$model"
      done
    fi
  done

  # Summary
  header "Next steps"
  echo "  • For internet-free syncing, deploy SSH keys:"
  echo "    ssh-copy-id ${SSH_USER}@<node>"
  echo ""
  echo "  • For permanent shared storage, mount NFS at:"
  echo "    /home/${SSH_USER}/.ollama/models/"
  echo "    on all nodes and bind it into the ollama container."
  echo ""
  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e " ${YELLOW}DRY RUN — no changes made${NC}"
  fi
}

main "$@"
