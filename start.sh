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

# ── 2. Generate olla.yaml from .env ──────────────────────────────────────────
bash "${SCRIPT_DIR}/scripts/generate-olla-config.sh"

# ── 3. Start the stack ────────────────────────────────────────────────
echo "→ Starting stack..."
docker compose up "$@"
