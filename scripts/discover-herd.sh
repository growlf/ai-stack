#!/usr/bin/env bash
# discover-herd.sh — mDNS discovery of Ollama nodes on LAN
#
# Uses avahi-browse to find _ollama._tcp services, then:
#   1. Writes discovered nodes to .env as OLLAMA_REMOTE_* entries
#   2. Regenerates olla.yaml via generate-olla-config.sh
#
# Also scans common ports (11434, 11435) on the local subnet as fallback.
#
# Usage:
#   ./scripts/discover-herd.sh              # scan, prompt before writing
#   ./scripts/discover-herd.sh --apply      # scan and write without prompt
#   ./scripts/discover-herd.sh --dry-run    # scan and print, don't write

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"

MODE="${1:---prompt}"
declare -a DISCOVERED=()

# ── mDNS discovery via avahi ────────────────────────────────────────────────
discover_mdns() {
    if ! command -v avahi-browse &>/dev/null; then
        return 1
    fi
    local services
    services=$(avahi-browse _ollama._tcp --resolve --terminate --parsable 2>/dev/null || true)
    if [[ -z "$services" ]]; then
        return 1
    fi
    while IFS=';' read -r _ _ _ _ host _ _ port _; do
        host="${host%%.*}"
        host="${host%local}"
        if [[ -n "$host" && -n "$port" ]]; then
            DISCOVERED+=("${host}=http://${host}:${port}")
        fi
    done <<< "$services"
}

# ── Subnet scan fallback ────────────────────────────────────────────────────
discover_subnet() {
    local iface
    iface=$(ip route get 1 | awk '{print $5; exit}')
    [[ -z "$iface" ]] && return 1
    local subnet
    subnet=$(ip -o -f inet addr show "$iface" | awk '{print $4}')
    [[ -z "$subnet" ]] && return 1
    local base="${subnet%.*}"
    echo "  scanning ${base}.0/24 ports 11434, 11435..." >&2
    for host in $(seq 1 254); do
        local ip="${base}.${host}"
        for port in 11434 11435; do
            if timeout 1 bash -c "echo > /dev/tcp/${ip}/${port}" 2>/dev/null; then
                local name="auto_${host}"
                DISCOVERED+=("${name}=http://${ip}:${port}")
            fi
        done
    done
}

# ── Write to .env ────────────────────────────────────────────────────────────
write_env() {
    local tmpf
    tmpf=$(mktemp)
    # Remove existing auto-discovered entries
    while IFS= read -r line; do
        if [[ "$line" =~ ^OLLAMA_REMOTE_auto_ ]]; then
            continue
        fi
        echo "$line" >> "$tmpf"
    done < "$ENV_FILE"
    # Append new discoveries
    for entry in "${DISCOVERED[@]}"; do
        local name="${entry%%=*}"
        local url="${entry#*=}"
        echo "OLLAMA_REMOTE_${name}=${url}" >> "$tmpf"
    done
    mv "$tmpf" "$ENV_FILE"
    echo "→ wrote ${#DISCOVERED[@]} discovered nodes to .env"
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    echo "→ Discovering Ollama nodes on LAN..."

    if discover_mdns; then
        echo "  mDNS: found ${#DISCOVERED[@]} node(s)"
    else
        echo "  mDNS: no _ollama._tcp services found (or avahi not running)"
    fi

    if [[ ${#DISCOVERED[@]} -eq 0 ]]; then
        echo "  falling back to subnet scan..."
        discover_subnet || true
        echo "  subnet scan: found ${#DISCOVERED[@]} node(s)"
    fi

    if [[ ${#DISCOVERED[@]} -eq 0 ]]; then
        echo "→ no remote Ollama nodes discovered"
        return 0
    fi

    echo ""
    for entry in "${DISCOVERED[@]}"; do
        echo "  ${entry%%=*} → ${entry#*=}"
    done
    echo ""

    if [[ "$MODE" == "--dry-run" ]]; then
        echo "→ dry-run — not writing"
        return 0
    fi

    if [[ "$MODE" == "--apply" ]]; then
        write_env
    else
        read -rp "Write these to .env and regenerate olla.yaml? [y/N] " reply
        if [[ "$reply" =~ ^[yY] ]]; then
            write_env
        else
            echo "→ skipped"
            return 0
        fi
    fi

    # Regenerate olla.yaml
    bash "${SCRIPT_DIR}/generate-olla-config.sh"
    echo "→ done — restart Olla to pick up changes: docker compose restart olla"
}

main
