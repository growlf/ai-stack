#!/usr/bin/env bash
# resolve-vaultwarden.sh
# Disabled: VaultWarden integration not currently configured
# To enable: set BW_CLIENT_ID, BW_CLIENT_SECRET, VAULT_MASTER_PASSWORD and uncomment below

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

echo "→ VaultWarden resolution disabled (no BW_ credentials set)"
echo "→ Using .env as-is"

# Uncomment to enable:
# if grep -q '<vaultwarden:' "$ENV_FILE" 2>/dev/null; then
#   echo "→ VaultWarden placeholders found - run manually:"
#   echo "   ./scripts/resolve-vaultwarden.sh --in-place"
# fi
