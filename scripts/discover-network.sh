#!/usr/bin/env bash
# discover-network.sh — scan LAN + VPN subnets for AI-stack services
#
# Finds Ollama, Olla, LiteLLM, and OpenCode instances on reachable networks.
# Verifies each found port by probing its API, then interactively prompts
# which discovered services to add and/or which existing entries to remove.
#
# Usage:
#   ./scripts/discover-network.sh              # scan, interactive add/remove
#   ./scripts/discover-network.sh --apply      # add all discovered, no prompt
#   ./scripts/discover-network.sh --dry-run    # scan and print, don't modify

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

ALL_PORTS="11434,11435,40114,4000,14096"

# ── 1. Discover subnets ──────────────────────────────────────────────────
discover_subnets() {
    local subnets=()
    while IFS= read -r line; do
        local iface addr
        iface=$(echo "$line" | awk '{print $2}' | cut -d: -f1)
        addr=$(echo "$line" | awk '{print $4}')
        [[ -z "$addr" ]] && continue
        ip=$(echo "$addr" | cut -d/ -f1)
        [[ "$iface" == lo ]] && continue
        [[ "$iface" == docker* ]] && continue
        [[ "$iface" == br-* ]] && continue
        [[ "$iface" == veth* ]] && continue
        [[ "$addr" == *:* ]] && continue
        local network
        network=$(ipcalc -n "$addr" 2>/dev/null | grep ^NETWORK | awk '{print $2}') || true
        if [[ -z "$network" ]]; then
            network=$(echo "$ip" | cut -d. -f1-3)".0/24"
        fi
        subnets+=("$network ($iface)")
    done < <(ip -o addr show 2>/dev/null | grep -v 'host lo')
    printf '%s\n' "${subnets[@]}"
}

# ── 2. Scan ports on a subnet ────────────────────────────────────────────
scan_subnet() {
    local subnet="$1"
    local subnet_only
    subnet_only=$(echo "$subnet" | cut -d' ' -f1)
    if command -v nmap &>/dev/null; then
        nmap -p "$ALL_PORTS" --open -T4 -n "${subnet_only}" 2>/dev/null | \
            awk '/^Nmap scan report for/{host=$NF} /^[0-9]+\/tcp/{print host ":" $1}' | \
            cut -d/ -f1
    else
        local base
        base=$(echo "$subnet_only" | sed 's/\.0\/.*$//')
        warn "nmap not found — falling back to slow sequential scan"
        for i in $(seq 1 254); do
            local host="${base}.${i}"
            for port in $(echo "$ALL_PORTS" | tr ',' ' '); do
                (echo >/dev/tcp/"${host}"/"${port}") 2>/dev/null && echo "${host}:${port}" &
            done
            wait
        done 2>/dev/null
    fi
}

# ── 3. Verify a discovered service ───────────────────────────────────────
verify_service() {
    local host="$1" port="$2"
    local base="http://${host}:${port}"
    local resp

    # Ollama
    if [[ "$port" == "11434" || "$port" == "11435" ]]; then
        resp=$(curl -sf "${base}/api/tags" 2>/dev/null || true)
        if echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
models=d.get('models',[])
if models:
    names=[m.get('name','') for m in models[:3]]
    print(f\"ollama|{', '.join(names)}|{len(models)} models\")
else:
    sys.exit(1)
" 2>/dev/null; then
            return
        fi
    fi

    # Olla
    if [[ "$port" == "40114" ]]; then
        resp=$(curl -sf "${base}/internal/health" 2>/dev/null || true)
        if echo "$resp" | grep -q '"status":"ok"'; then
            local ep_count
            ep_count=$(curl -sf "${base}/internal/status/endpoints" 2>/dev/null | python3 -c "
import sys,json; d=json.load(sys.stdin)
eps=d.get('endpoints',d.get('proxies',[]))
print(len(eps))
" 2>/dev/null || echo "?")
            echo "olla||${ep_count} endpoints"
            return
        fi
    fi

    # LiteLLM
    if [[ "$port" == "4000" ]]; then
        resp=$(curl -sf "${base}/health/liveness" 2>/dev/null || curl -sf "${base}/health" 2>/dev/null || true)
        if echo "$resp" | grep -q '"status":"ok"'; then
            local model_count
            model_count=$(curl -sf "${base}/v1/models" 2>/dev/null | python3 -c "
import sys,json; d=json.load(sys.stdin)
data=d.get('data',[])
print(len(data))
" 2>/dev/null || echo "?")
            echo "litellm||${model_count} models"
            return
        fi
    fi

    # OpenCode
    if [[ "$port" == "14096" ]]; then
        resp=$(curl -sf "${base}/" 2>/dev/null || true)
        if echo "$resp" | grep -qi 'opencode'; then
            echo "opencode||serve"
            return
        fi
    fi
}

# ── 4. List OLLAMA_REMOTE_* currently in .env ───────────────────────────
list_existing() {
    grep '^OLLAMA_REMOTE_' "$ENV_FILE" 2>/dev/null || true
}

# ── 5. Add a service ────────────────────────────────────────────────────
add_service() {
    local host="$1" port="$2" svc_type="$3" svc_name="$4"
    if [[ "$svc_type" != "ollama" ]]; then
        echo "skipped"
        return
    fi
    local name
    name=$(echo "${host}" | tr '.-' '_')
    local var="OLLAMA_REMOTE_${name}"
    if grep -q "^${var}=" "$ENV_FILE" 2>/dev/null; then
        echo "exists"
        return
    fi
    echo "OLLAMA_REMOTE_${name}=http://${host}:${port}" >> "$ENV_FILE"
    echo "added"
}

# ── 6. Remove an existing entry ─────────────────────────────────────────
remove_entry() {
    local line="$1"
    local var
    var=$(echo "$line" | cut -d= -f1)
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "/^${var}=/d" "$ENV_FILE"
    else
        sed -i "/^${var}=/d" "$ENV_FILE"
    fi
    echo "removed"
}

# ── 7. Interactive selection menu ───────────────────────────────────────
interactive_menu() {
    local discovered=("$@")
    local existing=()
    while IFS= read -r line; do
        existing+=("$line")
    done < <(list_existing)

    echo ""
    echo -e "${BOLD}${BLUE}Discovered AI Services${RESET}"
    echo -e "${BLUE}────────────────────────────────────────────────────────────────${RESET}"
    printf "  %-3s %-20s %-10s %-40s\n" "#" "Host:Port" "Type" "Details"
    echo "  ────────────────────────────────────────────────────────────────"
    local i=1
    for entry in "${discovered[@]}"; do
        IFS='|' read -r host port svc_type svc_name svc_ver <<< "$entry"
        local details="${svc_name}"
        [[ -n "$svc_ver" ]] && details="${svc_ver}"
        printf "  %-3d %-20s %-10s %-40s\n" "$i" "${host}:${port}" "${svc_type}" "${details}"
        i=$((i + 1))
    done

    if [[ ${#existing[@]} -gt 0 ]]; then
        echo ""
        echo -e "${YELLOW}Currently configured in .env:${RESET}"
        for line in "${existing[@]}"; do
            echo "    ${line}"
        done
    fi

    echo ""
    if [[ "$MODE" == "--dry-run" ]]; then
        info "Dry-run mode — no changes made"
        info "  Would prompt to add ${#discovered[@]} discovered and remove ${#existing[@]} configured"
        return
    fi

    local doing=""
    while true; do
        echo ""
        echo "  a <nums|all>  — add discovered services by number"
        echo "  r <nums|all>  — remove existing entries by number"
        if [[ ${#existing[@]} -eq 0 ]]; then
            echo "  d             — done"
        else
            echo "  d             — done (exit without changes)"
        fi
        echo ""
        read -rp "  Choose action [a/r/d]: " cmd args

        case "${cmd}" in
            a|add)
                if [[ -z "${args:-}" ]]; then
                    read -rp "    Enter numbers (e.g. 1,3,5 or 'all'): " args
                fi
                local added=0 skipped=0 existed=0
                if [[ "$args" == "all" ]]; then
                    for entry in "${discovered[@]}"; do
        IFS='|' read -r h p t n _ <<< "$entry"
                        result=$(add_service "$h" "$p" "$t" "$n")
                        case "$result" in
                            added)   (( added++ )) || true ;;
                            exists)  (( existed++ )) || true ;;
                            skipped) (( skipped++ )) || true ;;
                        esac
                    done
                else
                    local IFS=,
                    for num in $args; do
                        num=$(echo "$num" | xargs)
                        local idx=$((num - 1))
                        if [[ $idx -ge 0 && $idx -lt ${#discovered[@]} ]]; then
                            IFS='|' read -r h p t n _ <<< "${discovered[$idx]}"
                            result=$(add_service "$h" "$p" "$t" "$n")
                            case "$result" in
                                added)   ok "Added ${h}:${p} (${t})";  (( added++ )) || true ;;
                                exists)  warn "${h}:${p} already configured"; (( existed++ )) || true ;;
                                skipped) warn "${h}:${p} (${t}) — not an Ollama node, can't add via OLLAMA_REMOTE_*"; (( skipped++ )) || true ;;
                            esac
                        fi
                    done
                fi
                echo ""
                info "Result: ${added} added, ${existed} already present, ${skipped} skipped (non-Ollama)"
                doing="changed"
                ;;

            r|remove)
                if [[ -z "${args:-}" ]]; then
                    read -rp "    Enter numbers (1-${#existing[@]} or 'all'): " args
                fi
                if [[ ${#existing[@]} -eq 0 ]]; then
                    warn "No entries to remove"
                    continue
                fi
                local removed=0
                if [[ "$args" == "all" ]]; then
                    for line in "${existing[@]}"; do
                        local var
                        var=$(echo "$line" | cut -d= -f1)
                        if [[ "$(uname)" == "Darwin" ]]; then
                            sed -i '' "/^${var}=/d" "$ENV_FILE"
                        else
                            sed -i "/^${var}=/d" "$ENV_FILE"
                        fi
                        ok "Removed ${var}"
                        (( removed++ )) || true
                    done
                    existing=()
                else
                    # Remove in reverse order so indices stay valid
                    local IFS=,
                    local sorted_nums
                    sorted_nums=$(echo "$args" | tr ',' '\n' | sort -rn) || true
                    unset IFS
                    local nums=()
                    while IFS= read -r num; do
                        nums+=("$num")
                    done <<< "$sorted_nums"
                    for num in "${nums[@]}"; do
                        local idx=$((num - 1))
                        if [[ $idx -ge 0 && $idx -lt ${#existing[@]} ]]; then
                            local line="${existing[$idx]}"
                            local var
                            var=$(echo "$line" | cut -d= -f1)
                            if [[ "$(uname)" == "Darwin" ]]; then
                                sed -i '' "/^${var}=/d" "$ENV_FILE"
                            else
                                sed -i "/^${var}=/d" "$ENV_FILE"
                            fi
                            ok "Removed ${var}"
                            (( removed++ )) || true
                        fi
                    done
                    # Refresh existing list
                    existing=()
                    while IFS= read -r line; do
                        existing+=("$line")
                    done < <(list_existing)
                fi
                echo ""
                info "Removed ${removed} entry/entries"
                doing="changed"
                ;;

            d|done)
                if [[ "$doing" == "changed" ]]; then
                    echo ""
                    info "Regenerating Olla config..."
                    bash "${SCRIPT_DIR}/generate-olla-config.sh"
                    echo ""
                    ok "Done! Restart the stack: sudo systemctl restart ai-stack.service"
                else
                    info "No changes made"
                fi
                break
                ;;

            *)
                warn "Unknown action: ${cmd}. Use a, r, or d."
                ;;
        esac
    done
}

# ── Apply mode: add all discovered, no prompt ────────────────────────────
apply_all() {
    local discovered=("$@")
    local added=0 existed=0 skipped=0
    for entry in "${discovered[@]}"; do
        IFS='|' read -r h p t n _ <<< "$entry"
        result=$(add_service "$h" "$p" "$t" "$n")
        case "$result" in
            added)   (( added++ )) || true ;;
            exists)  (( existed++ )) || true ;;
            skipped) (( skipped++ )) || true ;;
        esac
    done
    echo ""
    info "Result: ${added} added, ${existed} already present, ${skipped} skipped (non-Ollama)"
    if [[ "$added" -gt 0 ]]; then
        info "Regenerating Olla config..."
        bash "${SCRIPT_DIR}/generate-olla-config.sh"
        ok "Done! Restart the stack: sudo systemctl restart ai-stack.service"
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────
main() {
    echo ""
    info "Scanning network for AI services (Ollama, Olla, LiteLLM, OpenCode)..."
    echo ""

    mapfile -t subnets < <(discover_subnets)
    if [[ ${#subnets[@]} -eq 0 ]]; then
        err "No non-local network interfaces found"
        exit 1
    fi
    info "Found ${#subnets[@]} subnet(s) to scan:"
    for s in "${subnets[@]}"; do echo "  - $s"; done
    echo ""

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

    if [[ ${#found[@]} -eq 0 ]]; then
        warn "No AI services found on any scanned subnet"
        exit 0
    fi

    if [[ "$MODE" == "--apply" ]]; then
        apply_all "${found[@]}"
    else
        interactive_menu "${found[@]}"
    fi
}

main "$@"
