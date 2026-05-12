#!/usr/bin/env bash
# resolve-vaultwarden.sh — resolve <vaultwarden:path> placeholders in .env
#
# Reads .env, finds <vaultwarden:organization/item> placeholders, fetches
# the actual values from Bitwarden via the `bw` CLI, and writes them back.
#
# Placeholder formats:
#   <vaultwarden:org-uuid/item-name>   — search item by name within an org
#   <vaultwarden:item-uuid>            — fetch item by its UUID directly
#
# Authentication (in order of precedence):
#   1. BW_CLIENT_ID + BW_CLIENT_SECRET + VAULT_MASTER_PASSWORD (API key)
#   2. Existing `bw` session (already logged in and unlocked)
#
# Usage:
#   ./scripts/resolve-vaultwarden.sh              # resolve in-place
#   ./scripts/resolve-vaultwarden.sh --dry-run    # show what would change
#
# Env vars for auth:
#   BW_SERVER_URL=<url>          (optional, self-hosted VaultWarden)
#   BW_CLIENT_ID=user.xxxxxx
#   BW_CLIENT_SECRET=...
#   VAULT_MASTER_PASSWORD=...    (used to unlock locked vault)
#
# NOTE: bw login --apikey is incompatible with self-hosted VaultWarden
# (the server doesn't return userDecryptionOptions). If API key login fails,
# the script falls through to the existing session. For fresh setups, run
# 'bw login' interactively first, or set BW_CLIENT_ID + BW_CLIENT_SECRET
# and the script will attempt --apikey (may fail on VaultWarden).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
DRY_RUN=false
RESOLVED=0
FAILED=0

if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "→ .env not found — nothing to resolve"
    exit 0
fi

# Check for placeholders
if ! grep -q '<vaultwarden:' "$ENV_FILE" 2>/dev/null; then
    echo "→ No <vaultwarden:...> placeholders found in .env"
    exit 0
fi

echo "→ Resolving <vaultwarden:...> placeholders in .env..."

# ── Check bw CLI ──────────────────────────────────────────────────────────
if ! command -v bw &>/dev/null; then
    echo "  Bitwarden CLI (bw) not found — skipping VaultWarden resolution."
    echo "  VaultWarden placeholders in .env will remain unresolved."
    echo "  To install bw: sudo npm install -g @bitwarden/cli"
    exit 0
fi

# ── Configure server URL (self-hosted VaultWarden) ────────────────────────
if [[ -n "${BW_SERVER_URL:-}" ]]; then
    current_server=$(bw config server 2>/dev/null || echo "")
    if [[ "$current_server" != "$BW_SERVER_URL" ]]; then
        echo "  Configuring server: ${BW_SERVER_URL}"
        bw config server "$BW_SERVER_URL" >/dev/null 2>&1
    fi
fi

# ── Authenticate ──────────────────────────────────────────────────────────
bw_login() {
    if [[ -n "${BW_CLIENT_ID:-}" && -n "${BW_CLIENT_SECRET:-}" ]]; then
        echo "  Authenticating with API key..."
        export BW_SESSION
        BW_SESSION=$(bw login --apikey --raw 2>/dev/null || true)
        if [[ -z "$BW_SESSION" ]]; then
            echo "✗ bw API key login failed. Check BW_CLIENT_ID and BW_CLIENT_SECRET."
            return 1
        fi
    elif bw status 2>/dev/null | grep -q '"status":"unlocked"'; then
        return 0
    elif bw status 2>/dev/null | grep -q '"status":"locked"'; then
        if [[ -n "${VAULT_MASTER_PASSWORD:-}" ]]; then
            export BW_SESSION
            BW_SESSION=$(echo "$VAULT_MASTER_PASSWORD" | bw unlock --raw 2>/dev/null || true)
            if [[ -z "$BW_SESSION" ]]; then
                echo "✗ Failed to unlock vault with VAULT_MASTER_PASSWORD."
                return 1
            fi
        else
            echo "✗ Vault is locked. Unlock and export the session token, then retry:"
            echo "    export BW_SESSION=\$(bw unlock --raw)"
            echo "    ./install.sh"
            return 1
        fi
    else
        echo "✗ Not logged in to Bitwarden."
        echo "  Set BW_CLIENT_ID and BW_CLIENT_SECRET, or run 'bw login' manually."
        return 1
    fi
}

if ! bw_login; then
    exit 1
fi

BW_STATUS=$(bw status 2>/dev/null)
if ! echo "$BW_STATUS" | grep -q '"status":"unlocked"'; then
    echo "✗ Cannot unlock Bitwarden vault."
    exit 1
fi

echo "  Bitwarden vault unlocked."

# ── Fetch item value ──────────────────────────────────────────────────────
# Usage: fetch_value <org-uuid-or-empty> <item-identifier>
# Returns the password / notes / first custom field value.
fetch_value() {
    local org="$1"
    local identifier="$2"
    local item_json

    # If identifier looks like a UUID (hex with dashes), try direct lookup first
    if [[ "$identifier" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
        item_json=$(bw get item "$identifier" 2>/dev/null || true)
    fi

    # Fall back to search
    if [[ -z "${item_json:-}" ]]; then
        if [[ -n "$org" ]]; then
            item_json=$(bw list items --search "$identifier" --organizationid "$org" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if isinstance(data, list):
    # Find best match: exact name match first, then partial
    term = '$identifier'.lower()
    for item in data:
        name = item.get('name', '').lower()
        if name == term:
            print(json.dumps(item))
            sys.exit(0)
    # No exact match — take first result
    if data:
        print(json.dumps(data[0]))
" 2>/dev/null || true)
        else
            item_json=$(bw list items --search "$identifier" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if isinstance(data, list) and data:
    print(json.dumps(data[0]))
" 2>/dev/null || true)
        fi
    fi

    if [[ -z "$item_json" ]]; then
        return 1
    fi

    # Extract value from the item — pipe JSON via stdin to avoid quoting issues
    echo "$item_json" | python3 -c "
import sys, json
item = json.load(sys.stdin)
# Priority: login.password > custom field named 'value' or 'secret' > notes
if 'login' in item and isinstance(item['login'], dict) and 'password' in item['login']:
    print(item['login']['password'], end='')
elif 'fields' in item and isinstance(item['fields'], list):
    for f in item['fields']:
        name = (f.get('name') or '').lower()
        if name in ('value', 'secret', 'key', 'token', 'apikey'):
            print(f.get('value', ''), end='')
            sys.exit(0)
    if item['fields']:
        print(item['fields'][0].get('value', ''), end='')
elif 'notes' in item and item.get('notes'):
    print(item['notes'], end='')
else:
    sys.exit(1)
" 2>/dev/null || return 1
}

# ── Resolve placeholders ──────────────────────────────────────────────────
TMP_ENV=$(mktemp)
trap 'rm -f "$TMP_ENV"' EXIT

while IFS= read -r line || [[ -n "$line" ]]; do
    # Skip comment lines
    [[ "$line" =~ ^[[:space:]]*# ]] && { echo "$line" >> "$TMP_ENV"; continue; }
    if [[ "$line" =~ \<vaultwarden:([^>]+)\> ]]; then
        placeholder="${BASH_REMATCH[0]}"
        path="${BASH_REMATCH[1]}"

        # Parse: org/item-name or just item-id
        if [[ "$path" == */* ]]; then
            org="${path%%/*}"
            identifier="${path#*/}"
        else
            org=""
            identifier="$path"
        fi

        echo "  Resolving: ${placeholder}"
        value=$(fetch_value "$org" "$identifier" || true)

        if [[ -n "$value" ]]; then
            line="${line//${placeholder}/${value}}"
            echo "    ✓ resolved"
            (( RESOLVED++ )) || true
        else
            echo "    ✗ could not fetch — leaving placeholder"
            (( FAILED++ )) || true
        fi
    fi
    echo "$line" >> "$TMP_ENV"
done < "$ENV_FILE"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "→ Dry-run: ${RESOLVED} placeholder(s) would be resolved"
    if command -v diff &>/dev/null; then
        diff --color=always "$ENV_FILE" "$TMP_ENV" 2>/dev/null || true
    fi
else
    cp "$TMP_ENV" "$ENV_FILE"
    echo "→ Resolved ${RESOLVED} placeholder(s) in .env"
fi

if [[ "$FAILED" -gt 0 ]]; then
    echo "→ ${FAILED} placeholder(s) could not be resolved."
    echo "  Create the items in Bitwarden and re-run this script."
    exit 1
fi
