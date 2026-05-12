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

# ── 1. Verify .env exists ─────────────────────────────────────────────
if [[ ! -f .env ]]; then
  echo "✗ .env not found. Copy .env.example and fill in your values:"
  echo "    cp .env.example .env && nano .env"
  exit 1
fi

# ── 2. Resolve VaultWarden placeholders (if any) ──────────────────────
bash "${SCRIPT_DIR}/scripts/resolve-vaultwarden.sh"

# ── 3. Generate olla.yaml from .env ───────────────────────────────────
bash "${SCRIPT_DIR}/scripts/generate-olla-config.sh"

# ── 4. Detect GPU and apply overlay ───────────────────────────────────
GPU_OVERLAY=""
if [[ -e /dev/dri/renderD128 ]]; then
  if [[ -f "${SCRIPT_DIR}/docker-compose.arc.yml" ]]; then
    GPU_OVERLAY="-f docker-compose.yml -f docker-compose.arc.yml"
    echo "→ Intel GPU detected — using Arc GPU overlay"
  fi
fi

# ── 5. Start the stack ────────────────────────────────────────────────
echo "→ Starting stack..."
if [[ -n "$GPU_OVERLAY" ]]; then
  docker compose $GPU_OVERLAY up "$@"
else
  docker compose up "$@"
fi
