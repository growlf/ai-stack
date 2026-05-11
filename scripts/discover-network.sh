#!/usr/bin/env bash
# discover-network.sh — scan LAN + VPN subnets for AI-stack services
#
# Finds Ollama, Olla, LiteLLM, and OpenCode instances on reachable networks.
# Verifies each found port by probing its API, then asks which to add.
#
# Usage:
#   ./scripts/discover-network.sh              # scan, prompt before writing
#   ./scripts/discover-network.sh --apply      # scan and write without prompt
#   ./scripts/discover-network.sh --dry-run    # scan and print, don't write

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"

MODE="${1:---prompt}"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
info()  { echo -e "${BLUE}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
err()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

# Ports to scan for each service type
declare -A SERVICE_PORTS
SERVICE_PORTS[ollama]="11434"
SERVICE_PORTS[olla]="40114"
SERVICE_PORTS[litellm]="4000"
SERVICE_PORTS[opencode]="14096"

# All unique ports
ALL_PORTS=""
for p in "${SERVICE_PORTS[@]}"; do
    ALL_PORTS="${ALL_PORTS}${ALL_PORTS:+,}${p}"
done
ALL_PORTS="${ALL_PORTS},11435"  # common alt Ollama port

# ── 1. Discover network interfaces and subnets ───────────────────────────
discover_subnets() {
    local subnets=()
    while IFS= read -r line; do
        local iface addr
        iface=$(echo "$line" | awk '{print $2}' | cut -d: -f1)
        addr=$(echo "$line" | awk '{print $4}')
        [[ -z "$addr" ]] && continue
        ip=$(echo "$addr" | cut -d/ -f1)
        # Skip loopback, docker bridges, veth pairs, IPv6
        [[ "$iface" == lo ]] && continue
        [[ "$iface" == docker* ]] && continue
        [[ "$iface" == br-* ]] && continue
        [[ "$iface" == veth* ]] && continue
        [[ "$addr" == *:* ]] && continue  # skip IPv6
        # Compute network from IP/CIDR
        local network
        network=$(ipcalc -n "$addr" 2>/dev/null | grep ^NETWORK | awk '{print $2}') || true
        if [[ -z "$network" ]]; then
            # Fallback: use first 3 octets for /24
            network=$(echo "$ip" | cut -d. -f1-3)".0/24"
        fi
        subnets+=("$network ($iface)")
    done < <(ip -o addr show 2>/dev/null | grep -v 'host lo')
    printf '%s\n' "${subnets[@]}"
}

# ── 2. Scan ports on a subnet ────────────────────────────────────────────
# Returns: host:port lines
scan_subnet() {
    local subnet="$1"
    local subnet_only
    subnet_only=$(echo "$subnet" | cut -d' ' -f1)
    local iface
    iface=$(echo "$subnet" | cut -d'(' -f2 | cut -d')' -f1)

    if command -v nmap &>/dev/null; then
        nmap -p "$ALL_PORTS" --open -T4 -n "${subnet_only}" 2>/dev/null | \
            awk '/^Nmap scan report for/{host=$NF} /^[0-9]+\/tcp/{print host ":" $1}' | \
            cut -d/ -f1
    else
        # Fallback: sequential nc scan (slow — warn user)
        local base
        base=$(echo "$subnet_only" | sed 's/\.0\/.*$//')
        warn "nmap not found — falling back to slow sequential scan"
        for i in $(seq 1 254); do
            local host="${base}.${i}"
            local IFS=","
            for port in $ALL_PORTS; do
                (echo >/dev/tcp/"${host}"/"${port}") 2>/dev/null && echo "${host}:${port}" &
            done
            wait
        done 2>/dev/null
    fi
}

# ── 3. Verify a discovered service ───────────────────────────────────────
# Returns: "TYPE|name|version" or empty if unverifiable
verify_service() {
    local host="$1"
    local port="$2"
    local base="http://${host}:${port}"

    # Ollama
    if [[ "$port" == "11434" || "$port" == "11435" ]]; then
        local resp
        resp=$(curl -sf "${base}/api/tags" 2>/dev/null || true)
        if echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('models',[{}])[0].get('name',''))" 2>/dev/null | grep -q .; then
            local model version
            model=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m.get('name','') for m in d.get('models',[])[:3]))" 2>/dev/null)
            version=$(echo "$resp" | python3 -c "
import sys,json; d=json.load(sys.stdin)
models=d.get('models',[])
if models:
    digest=models[0].get('digest','')[:12]
    print(f'ollama ({len(models)} models)')
else:
    print('ollama')
" 2>/dev/null)
            echo "ollama|${model}|${version}"
            return
        fi
    fi

    # Olla
    if [[ "$port" == "40114" ]]; then
        local resp
        resp=$(curl -sf "${base}/internal/health" 2>/dev/null || true)
        if echo "$resp" | grep -q '"status":"ok"'; then
            local endpoints
            endpoints=$(curl -sf "${base}/internal/status/endpoints" 2>/dev/null | python3 -c "
import sys,json; d=json.load(sys.stdin)
eps=d.get('endpoints',d.get('proxies',[]))
print(f'{len(eps)} endpoints')
" 2>/dev/null || echo "unknown")
            echo "olla|${endpoints}|"
            return
        fi
    fi

    # LiteLLM
    if [[ "$port" == "4000" ]]; then
        local resp
        resp=$(curl -sf "${base}/health/liveness" 2>/dev/null || curl -sf "${base}/health" 2>/dev/null || true)
        if echo "$resp" | grep -q '"status":"ok"'; then
            local models
            models=$(curl -sf "${base}/v1/models" 2>/dev/null | python3 -c "
import sys,json; d=json.load(sys.stdin)
data=d.get('data',[])
print(f'{len(data)} models')
" 2>/dev/null || echo "unknown")
            echo "litellm|${models}|"
            return
        fi
    fi

    # OpenCode serve
    if [[ "$port" == "14096" ]]; then
        local resp
        resp=$(curl -sf "${base}/" 2>/dev/null || true)
        if echo "$resp" | grep -qi 'opencode'; then
            echo "opencode|serve|"
            return
        fi
    fi

    # Generic Ollama check (port 11434 without /api/tags)
    if [[ "$port" == "11434" || "$port" == "11435" ]]; then
        if curl -sf "${base}/" >/dev/null 2>&1; then
            echo "ollama|unknown|"
            return
        fi
    fi
}

# ── 4. Present results ──────────────────────────────────────────────────
print_table() {
    local results=("$@")
    echo ""
    echo -e "${BOLD}${BLUE}Discovered AI Services${RESET}"
    echo -e "${BLUE}────────────────────────────────────────────────────────${RESET}"
    printf "  %-18s %-8s %-10s %-30s\n" "Host" "Port" "Type" "Details"
    echo "  ─────────────────────────────────────────────────────"
    for entry in "${results[@]}"; do
        IFS='|' read -r host port svc_type svc_name svc_ver <<< "$entry"
        local details="${svc_name}"
        [[ -n "$svc_ver" ]] && details="${details} — ${svc_ver}"
        printf "  %-18s %-8s %-10s %-30s\n" "${host}:${port}" "" "${svc_type}" "${details}"
    done
    echo ""
}

# ── 5. Add to Olla config ───────────────────────────────────────────────
add_to_olla() {
    local host="$1"
    local port="$2"
    local svc_type="$3"
    # Write OLLAMA_REMOTE_* for Ollama nodes; others need different handling
    if [[ "$svc_type" == "ollama" ]]; then
        local name
        name=$(echo "$host" | tr '.-' '_')
        echo "OLLAMA_REMOTE_${name}=http://${host}:${port}" >> "$ENV_FILE"
        ok "Added OLLAMA_REMOTE_${name}=http://${host}:${port} to .env"
    else
        warn "Skipping ${svc_type} — only Ollama nodes are added via OLLAMA_REMOTE_*"
        info "  Olla and LiteLLM on the same subnet are routed through Olla automatically"
        info "  if Olla's model_discovery is enabled (default: every 5m)"
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────
main() {
    echo ""
    info "Scanning network for AI services (Ollama, Olla, LiteLLM, OpenCode)..."
    echo ""

    # Get subnets
    mapfile -t subnets < <(discover_subnets)
    if [[ ${#subnets[@]} -eq 0 ]]; then
        err "No non-local network interfaces found"
        exit 1
    fi
    info "Found ${#subnets[@]} subnet(s) to scan:"
    for s in "${subnets[@]}"; do echo "  - $s"; done
    echo ""

    # Scan each subnet
    declare -a found
    for subnet in "${subnets[@]}"; do
        local subnet_only
        subnet_only=$(echo "$subnet" | cut -d' ' -f1)
        info "Scanning ${subnet_only}..."
        while IFS= read -r result; do
            [[ -z "$result" ]] && continue
            local host port
            host=$(echo "$result" | cut -d: -f1)
            port=$(echo "$result" | cut -d: -f2)
            info "  Verifying ${host}:${port}..."
            local verified
            verified=$(verify_service "$host" "$port" || true)
            if [[ -n "$verified" ]]; then
                found+=("${host}|${port}|${verified}")
                local svc_type
                svc_type=$(echo "$verified" | cut -d'|' -f1)
                ok "  ${host}:${port} = ${svc_type}"
            fi
        done < <(scan_subnet "$subnet" 2>/dev/null || true)
    done

    # Show results
    if [[ ${#found[@]} -eq 0 ]]; then
        warn "No AI services found on any scanned subnet"
        exit 0
    fi

    print_table "${found[@]}"

    # Prompt or apply
    if [[ "$MODE" == "--dry-run" ]]; then
        info "Dry-run — would prompt to add ${#found[@]} service(s)"
        exit 0
    fi

    local do_add="y"
    if [[ "$MODE" != "--apply" ]]; then
        echo ""
        read -rp "Add discovered Ollama nodes to Olla config? [y/N] " do_add
    fi

    if [[ "${do_add,,}" == "y" ]]; then
        local added=0
        for entry in "${found[@]}"; do
            IFS='|' read -r host port svc_type svc_name svc_ver <<< "$entry"
            if add_to_olla "$host" "$port" "$svc_type"; then
                (( added++ )) || true
            fi
        done
        if [[ "$added" -gt 0 ]]; then
            info "Regenerating Olla config..."
            bash "${SCRIPT_DIR}/generate-olla-config.sh"
            info "Restart the stack to apply: sudo systemctl restart ai-stack.service"
        fi
        echo ""
        ok "Added ${added} service(s)"
    else
        info "Skipping — nothing added"
    fi
}

main "$@"
