#!/usr/bin/env bash
# generate-keys.sh
#
# Generates cryptographically secure keys for ai-stack services.
# Output can be copy-pasted into .env or used with VaultWarden.
#
# Usage:
#   ./scripts/generate-keys.sh              # print all keys
#   ./scripts/generate-keys.sh --webui      # print only WEBUI_SECRET_KEY
#   ./scripts/generate-keys.sh --khoj       # print only KHOJ_DJANGO_SECRET_KEY
#   ./scripts/generate-keys.sh --litellm     # print only LITELLM_MASTER_KEY

set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; RESET='\033[0m'

gen_webui() {
  echo -e "${BLUE}WEBUI_SECRET_KEY (Fernet - 32 url-safe base64 bytes):${RESET}"
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  echo ""
}

gen_khoj() {
  echo -e "${BLUE}KHOJ_DJANGO_SECRET_KEY (Django secret - 50 chars):${RESET}"
  python3 -c "import secrets; print(secrets.token_urlsafe(37))"
  echo ""
}

gen_litellm() {
  echo -e "${BLUE}LITELLM_MASTER_KEY (API key - 32 chars):${RESET}"
  python3 -c "import secrets; print('sk-local-' + secrets.token_hex(16))"
  echo ""
}

gen_all() {
  echo -e "${GREEN}═══ Generated Keys (copy to .env or VaultWarden) ═══${RESET}\n"
  gen_webui
  gen_khoj
  gen_litellm
  echo -e "${BLUE}PIPELINES_API_KEY / OPEN_TERMINAL_API_KEY (32 chars):${RESET}"
  python3 -c "import secrets; print(secrets.token_hex(16))"
  echo ""
}

if [[ "${1:-}" == "--webui" ]]; then
  gen_webui
elif [[ "${1:-}" == "--khoj" ]]; then
  gen_khoj
elif [[ "${1:-}" == "--litellm" ]]; then
  gen_litellm
else
  gen_all
fi
