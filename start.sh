#!/usr/bin/env bash
# start.sh — pre-flight wrapper for the AI stack
#
# Run this instead of `docker compose up` directly.
# It handles:
#   1. Checking .env exists
#   2. Resolving <vaultwarden:...> placeholders (if present)
#   3. Generating olla.yaml from OLLAMA_REMOTE_* entries in .env
#   4. Passing any extra args through to docker compose
#
# Examples:
#   ./start.sh                  # bring up stack in foreground
#   ./start.sh -d               # detached (background)
#   ./start.sh -d --build       # rebuild images and detach
#   ./start.sh down             # tear down the stack
#
# VaultWarden Integration:
#   If .env contains <vaultwarden:path> placeholders, they will be
#   resolved using the `bw` CLI before starting the stack.
#   Ensure `bw` is logged in and unlocked, or set BW_CLIENT_ID,
#   BW_CLIENT_SECRET, and VAULT_MASTER_PASSWORD environment variables.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# ── 1. Ensure .env exists (auto-bootstrap on first run) ───────────────
if [[ ! -f .env ]]; then
  if [[ ! -f .env.example ]]; then
    echo "✗ .env.example not found. Is this a complete ai-stack clone?"
    exit 1
  fi
  echo "→ No .env found — creating from .env.example..."
  cp .env.example .env
  sed -i "s|^STACK_USER=.*|STACK_USER=$(whoami)|" .env
  LITELLM_KEY=$(python3 -c "import secrets; print('sk-local-' + secrets.token_hex(16))" 2>/dev/null \
      || openssl rand -hex 24 | awk '{print "sk-local-" $0}')
  sed -i "s|^LITELLM_MASTER_KEY=.*|LITELLM_MASTER_KEY=${LITELLM_KEY}|" .env
  echo "✓ Created .env — STACK_USER=$(whoami), LITELLM_MASTER_KEY auto-generated."
  echo "  For cloud models (Claude/Gemini), add API keys to .env first."
  echo ""
fi

# ── 2. Resolve VaultWarden placeholders (if any) ──────────────────────
if grep -v '^[[:space:]]*#' "${SCRIPT_DIR}/.env" 2>/dev/null | grep -q '<vaultwarden:'; then
  if [[ -z "${BW_SESSION:-}" ]] && [[ -z "${VAULT_MASTER_PASSWORD:-}" ]]; then
    echo "  Your .env has VaultWarden secrets — unlock first:"
    echo "    export BW_SESSION=\$(bw unlock --raw)"
    echo "    ./start.sh"
  fi
  bash "${SCRIPT_DIR}/scripts/resolve-vaultwarden.sh"
fi

# ── 3. Generate olla.yaml from .env ───────────────────────────────────
bash "${SCRIPT_DIR}/scripts/generate-olla-config.sh"

# ── 4. Detect GPU and apply overlay ───────────────────────────────────
# Explicit GPU_TYPE in .env takes priority over auto-detection.
source .env 2>/dev/null || true
GPU_TYPE="${GPU_TYPE:-}"

if [[ -z "$GPU_TYPE" ]]; then
  if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null 2>&1; then
    GPU_TYPE="nvidia"
  elif ls /dev/dri/card* &>/dev/null 2>&1; then
    if command -v lspci &>/dev/null && lspci 2>/dev/null | grep -iq "Intel.*Arc"; then
      GPU_TYPE="arc"
    fi
  fi
  GPU_TYPE="${GPU_TYPE:-cpu}"
fi

case "$GPU_TYPE" in
  arc)
    COMPOSE_ARGS="-f docker-compose.yml -f docker-compose.arc.yml"
    echo "→ Intel Arc GPU — using Arc overlay"
    ;;
  nvidia)
    COMPOSE_ARGS="-f docker-compose.yml -f docker-compose.nvidia.yml"
    echo "→ NVIDIA GPU — using NVIDIA overlay"
    ;;
  *)
    COMPOSE_ARGS="-f docker-compose.yml"
    echo "→ CPU-only mode"
    ;;
esac

# ── 5. Start the stack ────────────────────────────────────────────────
echo "→ Starting stack..."
docker compose $COMPOSE_ARGS up "$@"
