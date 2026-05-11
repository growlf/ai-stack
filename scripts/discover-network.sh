#!/usr/bin/env bash
# discover-network.sh — discover AI services on LAN and VPN networks
#
# Strategy:
#   1. Accept seed hosts from user (host:port) — these unlock VPN subnets
#   2. Verify each seed via API probe (Ollama/Olla/LiteLLM/OpenCode)
#   3. If Olla: harvest its endpoint list for known node names
#   4. Scan each seed's /24 subnet for more services (works over VPN)
#   5. Auto-detect LAN /24 subnets (skips VPN — only seeds unlock those)
#   6. Merge, verify, deduplicate, let user pick which to add/remove
#
# Usage:
#   ./scripts/discover-network.sh                         # interactive
#   ./scripts/discover-network.sh 10.10.0.201:11434       # seed(s) as args
#   ./scripts/discover-network.sh --apply                 # add all, no prompt
#   ./scripts/discover-network.sh --dry-run               # preview only

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"

MODE="${1:---prompt}"
# If first arg is a seed (contains a colon), treat all args as seeds
if [[ "$1" == *:* ]]; then
    SEEDS=("$@")
    MODE="--prompt"
elif [[ "$1" == "--apply" || "$1" == "--dry-run" ]]; then
    SEEDS=("${@:2}")
else
    SEEDS=()
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
info()  { echo -e "${BLUE}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
err()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

ALL_PORTS="11434,11435,40114,4000,14096"
TARGET_PORTS=(11434 11435 40114 4000 14096)

# ── Collect local IPs for same-machine filtering ──────────────────────────
get_local_ips() {
    hostname -I 2>/dev/null | tr ' ' '\n'
    ip -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -v ':'
}

# ── Verify a host:port and return "TYPE|details" ──────────────────────────
verify_service() {
    local host="$1" port="$2"
    local base="http://${host}:${port}"

    # Ollama
    if [[ "$port" == "11434" || "$port" == "11435" ]]; then
        local resp
        resp=$(curl -sf --max-time 5 "${base}/api/tags" 2>/dev/null || true)
        if echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
models=d.get('models',[])
if models:
    names=[m.get('name','') for m in models[:4]]
    print(f'ollama|{\", \".join(names)}|{len(models)} models')
else:
    sys.exit(1)
" 2>/dev/null; then
            return
        fi
    fi

    # Olla
    if [[ "$port" == "40114" ]]; then
        local resp
        resp=$(curl -sf --max-time 5 "${base}/internal/health" 2>/dev/null || true)
        if echo "$resp" | grep -q '"status":"ok"\|"status":"healthy"'; then
            local ep_info
            ep_info=$(curl -sf --max-time 5 "${base}/internal/status/endpoints" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
eps=d.get('endpoints',[])
# Format: name:type:status:models
parts=[]
for e in eps:
    name=e.get('name','?')
    st=e.get('status','?')
    mc=e.get('model_count','?')
    parts.append(f'{name}={st}({mc})')
print(' | '.join(parts))
" 2>/dev/null || echo "?")
            echo "olla||endpoints: ${ep_info}"
            return
        fi
    fi

    # LiteLLM
    if [[ "$port" == "4000" ]]; then
        local resp
        resp=$(curl -sf --max-time 5 "${base}/health/liveness" 2>/dev/null || \
               curl -sf --max-time 5 "${base}/health" 2>/dev/null || true)
        if echo "$resp" | grep -q '"status":"ok"'; then
            local mc
            mc=$(curl -sf --max-time 5 "${base}/v1/models" 2>/dev/null | python3 -c "
import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))
" 2>/dev/null || echo "?")
            echo "litellm||${mc} models"
            return
        fi
    fi

    # OpenCode
    if [[ "$port" == "14096" ]]; then
        local resp
        resp=$(curl -sf --max-time 5 "${base}/" 2>/dev/null || true)
        if echo "$resp" | grep -qi 'opencode'; then
            echo "opencode||serve"
            return
        fi
    fi
}

# ── Verify and label a discovered host ────────────────────────────────────
# Returns "host|port|type|details" or empty string
discover_and_label() {
    local host="$1" port="$2"
    local verified
    verified=$(verify_service "$host" "$port" || true)
    if [[ -n "$verified" ]]; then
        echo "${host}|${port}|${verified}"
    fi
}

# ── Scan a /24 subnet for target ports ────────────────────────────────────
scan_24() {
    local subnet="$1"
    if command -v nmap &>/dev/null; then
        nmap -p "$ALL_PORTS" --open -T4 -n "${subnet}" 2>/dev/null | \
            awk '/^Nmap scan report for/{host=$NF} /^[0-9]+\/tcp/{print host ":" $1}' | \
            cut -d/ -f1
    else
        local base
        base=$(echo "$subnet" | sed 's/\.0\/24$//;s/\.0$//')
        warn "nmap not found — slow scan"
        for i in $(seq 1 254); do
            for port in "${TARGET_PORTS[@]}"; do
                (echo >/dev/tcp/"${base}.${i}"/"${port}") 2>/dev/null && echo "${base}.${i}:${port}" &
            done
            wait
        done 2>/dev/null
    fi
}

# ── Get /24 subnet from an IP ─────────────────────────────────────────────
ip_to_24() {
    local ip="$1"
    echo "$(echo "$ip" | cut -d. -f1-3).0/24"
}

# ── Check if a subnet is a VPN interface ──────────────────────────────────
is_vpn_subnet() {
    local subnet="$1"
    # Check routing table for VPN interfaces
    ip route show table all 2>/dev/null | grep -E "dev (wg|tun|wt|tailscale)" | awk '{print $1}' | \
        grep -q "${subnet}" || return 1
}

# ── Auto-detect LAN subnets (non-VPN /24s) ────────────────────────────────
detect_lan_subnets() {
    local subnets=()
    local added=""
    while IFS= read -r line; do
        local dest dev
        dest=$(echo "$line" | awk '{print $1}')
        dev=$(echo "$line" | awk '{print $3}')
        [[ "$dest" == "default" ]] && continue
        [[ "$dest" == *:* ]] && continue
        [[ "$dest" != */24 ]] && continue
        [[ "$dev" == docker* ]] && continue
        [[ "$dev" == br-* ]] && continue
        [[ "$dev" == veth* ]] && continue
        [[ "$dev" == lo ]] && continue
        [[ "$dev" == lxcbr* ]] && continue
        [[ "$dev" == virbr* ]] && continue
        [[ "$dest" == 172.* ]] && continue
        # Skip VPN interfaces
        [[ "$dev" == wg* ]] && continue
        [[ "$dev" == tun* ]] && continue
        [[ "$dev" == wt* ]] && continue
        if echo "$added" | grep -q "${dest} "; then
            continue
        fi
        added="${added} ${dest} "
        subnets+=("${dest} (${dev})")
    done < <(ip route show table all 2>/dev/null | grep -v 'unreachable\|prohibit\|broadcast\|local\|fe80')
    printf '%s\n' "${subnets[@]}"
}

# ── List OLLAMA_REMOTE_* currently in .env ───────────────────────────────
list_existing() {
    grep '^OLLAMA_REMOTE_' "$ENV_FILE" 2>/dev/null || true
}

# ── Add a service ────────────────────────────────────────────────────────
add_service() {
    local host="$1" port="$2" svc_type="$3"
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

# ── Interactive selection menu ───────────────────────────────────────────
interactive_menu() {
    local discovered=("$@")
    local existing=()
    while IFS= read -r line; do
        existing+=("$line")
    done < <(list_existing)

    echo ""
    echo -e "${BOLD}${BLUE}Discovered AI Services${RESET}"
    echo -e "${BLUE}────────────────────────────────────────────────────────────────${RESET}"
    printf "  %-3s %-21s %-10s %-45s\n" "#" "Host:Port" "Type" "Details"
    echo "  ────────────────────────────────────────────────────────────────"
    local i=1
    for entry in "${discovered[@]}"; do
        IFS='|' read -r host port svc_type svc_name svc_ver <<< "$entry"
        local details="${svc_name}"
        [[ -n "$svc_ver" ]] && details="${svc_ver}"
        printf "  %-3d %-21s %-10s %-45s\n" "$i" "${host}:${port}" "${svc_type}" "${details}"
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
        info "Dry-run — no changes"
        info "  Would prompt to add ${#discovered[@]} discovered and remove ${#existing[@]} configured"
        return
    fi

    local doing=""
    while true; do
        echo ""
        echo "  a <nums|all>  — add discovered services by number"
        echo "  r <nums|all>  — remove existing OLLAMA_REMOTE_* entries"
        echo "  d             — done (regenerate + restart hint)"
        echo ""
        read -rp "  Choose action [a/r/d]: " cmd args

        case "${cmd}" in
            a|add)
                if [[ -z "${args:-}" ]]; then
                    read -rp "    Enter numbers (e.g. 1,3,5 or 'all'): " args
                fi
                local added=0 existed=0 skipped=0
                if [[ "$args" == "all" ]]; then
                    for entry in "${discovered[@]}"; do
                        IFS='|' read -r h p t _ _ <<< "$entry"
                        result=$(add_service "$h" "$p" "$t")
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
                            IFS='|' read -r h p t _ _ <<< "${discovered[$idx]}"
                            result=$(add_service "$h" "$p" "$t")
                            case "$result" in
                                added)   ok "Added ${h}:${p} (${t})";  (( added++ )) || true ;;
                                exists)  warn "${h}:${p} already in .env"; (( existed++ )) || true ;;
                                skipped) warn "${h}:${p} (${t}) — not Ollama, can't add via OLLAMA_REMOTE_*"; (( skipped++ )) || true ;;
                            esac
                        fi
                    done
                fi
                info "Result: ${added} added, ${existed} exists, ${skipped} skipped"
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
                        local var; var=$(echo "$line" | cut -d= -f1)
                        sed -i "/^${var}=/d" "$ENV_FILE"
                        ok "Removed ${var}"
                        (( removed++ )) || true
                    done
                    existing=()
                else
                    # Parse and sort descending
                    local sorted_nums
                    sorted_nums=$(echo "$args" | tr ',' '\n' | sort -rn) || true
                    while IFS= read -r num; do
                        num=$(echo "$num" | xargs)
                        [[ -z "$num" ]] && continue
                        local idx=$((num - 1))
                        if [[ $idx -ge 0 && $idx -lt ${#existing[@]} ]]; then
                            local var; var=$(echo "${existing[$idx]}" | cut -d= -f1)
                            sed -i "/^${var}=/d" "$ENV_FILE"
                            ok "Removed ${var}"
                            (( removed++ )) || true
                        fi
                    done <<< "$sorted_nums"
                    existing=()
                    while IFS= read -r line; do existing+=("$line"); done < <(list_existing)
                fi
                info "Removed ${removed} entry/entries"
                doing="changed"
                ;;

            d|done)
                if [[ "$doing" == "changed" ]]; then
                    echo ""
                    info "Regenerating Olla config..."
                    bash "${SCRIPT_DIR}/generate-olla-config.sh"
                    echo ""
                    ok "Done! Restart stack: sudo systemctl restart ai-stack.service"
                else
                    info "No changes made"
                fi
                break
                ;;

            *) warn "Unknown action: ${cmd}. Use a, r, or d." ;;
        esac
    done
}

# ── Apply all discovered ─────────────────────────────────────────────────
apply_all() {
    local discovered=("$@")
    local added=0 existed=0 skipped=0
    for entry in "${discovered[@]}"; do
        IFS='|' read -r h p t _ _ <<< "$entry"
        result=$(add_service "$h" "$p" "$t")
        case "$result" in
            added)   (( added++ )) || true ;;
            exists)  (( existed++ )) || true ;;
            skipped) (( skipped++ )) || true ;;
        esac
    done
    info "Result: ${added} added, ${existed} exists, ${skipped} skipped"
    if [[ "$added" -gt 0 ]]; then
        info "Regenerating Olla config..."
        bash "${SCRIPT_DIR}/generate-olla-config.sh"
        ok "Done! Restart stack: sudo systemctl restart ai-stack.service"
    fi
}

# ── Deduplicate discovered entries ───────────────────────────────────────
deduplicate() {
    local entries=("$@")
    local seen=""
    for entry in "${entries[@]}"; do
        local key
        key=$(echo "$entry" | cut -d'|' -f1-2)
        if echo "$seen" | grep -q "${key} "; then
            continue
        fi
        seen="${seen} ${key} "
        echo "$entry"
    done
}

# ── Main ─────────────────────────────────────────────────────────────────
main() {
    echo ""
    info "AI Service Discovery"
    echo ""

    # Collect local IPs
    local local_ips=()
    while IFS= read -r ip; do
        ip=$(echo "$ip" | xargs)
        [[ -n "$ip" ]] && local_ips+=("$ip")
    done < <(get_local_ips)

    # ── Step 1: Seeds ──────────────────────────────────────────────────
    if [[ ${#SEEDS[@]} -eq 0 && "$MODE" != "--apply" && "$MODE" != "--dry-run" ]]; then
        echo ""
        read -rp "Enter known AI host:port (space-separated, e.g. '10.10.0.201:11434'), or press Enter to auto-scan LAN: " seed_input
        if [[ -n "$seed_input" ]]; then
            IFS=' ' read -ra SEEDS <<< "$seed_input"
        fi
    fi

    declare -a all_found

    if [[ ${#SEEDS[@]} -gt 0 ]]; then
        info "Processing ${#SEEDS[@]} seed(s)..."
        local seeds_to_expand=()
        for seed in "${SEEDS[@]}"; do
            local host port
            host=$(echo "$seed" | cut -d: -f1)
            port=$(echo "$seed" | cut -d: -f2)
            [[ -z "$port" ]] && port="11434"
            info "Verifying seed ${host}:${port}..."
            local result
            result=$(discover_and_label "$host" "$port" || true)
            if [[ -n "$result" ]]; then
                all_found+=("$result")
                local svc_type
                svc_type=$(echo "$result" | cut -d'|' -f3)
                ok "  ${host}:${port} = ${svc_type}"
                # Check other ports on the same host (co-located services)
                for p in "${TARGET_PORTS[@]}"; do
                    if [[ "$p" != "$port" ]]; then
                        local extra
                        extra=$(discover_and_label "$host" "$p" || true)
                        if [[ -n "$extra" ]]; then
                            all_found+=("$extra")
                            local et; et=$(echo "$extra" | cut -d'|' -f3)
                            ok "  ${host}:${p} = ${et} (co-located)"
                        fi
                    fi
                done
                seeds_to_expand+=("$host")
            else
                warn "  ${host}:${port} — no response or unknown service"
            fi
        done

        # ── Step 2: Expand from seeds (scan their /24) ────────────────
        local expanded_networks=""
        for shost in "${seeds_to_expand[@]}"; do
            local subnet_24
            subnet_24=$(ip_to_24 "$shost")
            if echo "$expanded_networks" | grep -q "${subnet_24} "; then
                continue
            fi
            expanded_networks="${expanded_networks} ${subnet_24} "
            info "Expanding: scanning ${subnet_24}..."
            while IFS= read -r hit; do
                [[ -z "$hit" ]] && continue
                local h p
                h=$(echo "$hit" | cut -d: -f1)
                p=$(echo "$hit" | cut -d: -f2)
                # Skip local machine
                local skip=false
                for lip in "${local_ips[@]}"; do [[ "$h" == "$lip" ]] && skip=true && break; done
                [[ "$skip" == "true" ]] && { warn "  ${h}:${p} — local host, skip"; continue; }
                local result
                result=$(discover_and_label "$h" "$p" || true)
                if [[ -n "$result" ]]; then
                    all_found+=("$result")
                    local st; st=$(echo "$result" | cut -d'|' -f3)
                    ok "  ${h}:${p} = ${st}"
                fi
            done < <(scan_24 "$subnet_24" 2>/dev/null || true)
        done
    fi

    # ── Step 3: Auto-detect LAN ───────────────────────────────────────
    info "Auto-detecting LAN subnets..."
    local lan_subnets=()
    while IFS= read -r subnet; do
        lan_subnets+=("$subnet")
    done < <(detect_lan_subnets)

    if [[ ${#lan_subnets[@]} -gt 0 ]]; then
        info "Scanning ${#lan_subnets[@]} LAN subnet(s)..."
        for subnet in "${lan_subnets[@]}"; do
            local sn
            sn=$(echo "$subnet" | cut -d' ' -f1)
            info "  ${sn}..."
            while IFS= read -r hit; do
                [[ -z "$hit" ]] && continue
                local h p
                h=$(echo "$hit" | cut -d: -f1)
                p=$(echo "$hit" | cut -d: -f2)
                local skip=false
                for lip in "${local_ips[@]}"; do [[ "$h" == "$lip" ]] && skip=true && break; done
                [[ "$skip" == "true" ]] && continue
                local result
                result=$(discover_and_label "$h" "$p" || true)
                if [[ -n "$result" ]]; then
                    all_found+=("$result")
                fi
            done < <(scan_24 "$sn" 2>/dev/null || true)
        done
    fi

    # ── Step 4: Deduplicate ───────────────────────────────────────────
    declare -a final=()
    while IFS= read -r entry; do
        final+=("$entry")
    done < <(deduplicate "${all_found[@]}")

    if [[ ${#final[@]} -eq 0 ]]; then
        warn "No AI services found"
        exit 0
    fi

    echo ""
    info "Found ${#final[@]} unique service(s)"

    # ── Step 5: Present and act ───────────────────────────────────────
    if [[ "$MODE" == "--apply" ]]; then
        apply_all "${final[@]}"
    else
        interactive_menu "${final[@]}"
    fi
}

main "$@"
