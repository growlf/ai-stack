#!/usr/bin/env bash
# generate-keys.sh
#
# Generates cryptographically secure keys for ai-stack services.
# Output can be copy-pasted into .env.
#
# Usage:
#   ./scripts/generate-keys.sh              # print all keys
#   ./scripts/generate-keys.sh --litellm    # print only LITELLM_MASTER_KEY

set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; RESET='\033[0m'

gen_litellm() {
  echo -e "${BLUE}LITELLM_MASTER_KEY (API key - 32 chars):${RESET}"
  python3 -c "import secrets; print('sk-local-' + secrets.token_hex(16))"
  echo ""
}

gen_all() {
  echo -e "${GREEN}═══ Generated Keys (copy to .env) ═══${RESET}\n"
  gen_litellm
}

if [[ "${1:-}" == "--litellm" ]]; then
  gen_litellm
else
  gen_all
fi
