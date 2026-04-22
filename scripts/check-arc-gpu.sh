#!/usr/bin/env bash
# ─── check-arc-gpu.sh ─────────────────────────────────────────────────────────
# Pre-flight check for Intel Arc GPU before starting the AI stack.
# Detects the correct /dev/dri/cardN node and validates GPU signal stability.
# Used as ExecStartPre in ai-stack.service.
#
# Exit codes:
#   0 — GPU found and stable
#   1 — GPU not found or unstable

set -euo pipefail

RENDER_NODE="/dev/dri/renderD128"
SIGNAL_WAIT=2   # seconds to wait for signal stability
MAX_RETRIES=3

log()  { echo "[Signal] $*"; }
pass() { echo "[+] $*"; }
fail() { echo "[!] $*" >&2; exit 1; }

# ─── Find the Arc card node ───────────────────────────────────────────────────
find_arc_card() {
    for card in /dev/dri/card*; do
        [[ -e "$card" ]] || continue
        local cardnum="${card##*card}"
        local sysfs="/sys/class/drm/card${cardnum}/device"
        if [[ -f "${sysfs}/vendor" ]]; then
            local vendor
            vendor=$(cat "${sysfs}/vendor" 2>/dev/null || echo "")
            # Intel vendor ID is 0x8086
            if [[ "$vendor" == "0x8086" ]]; then
                echo "$card"
                return 0
            fi
        fi
    done
    return 1
}

# ─── Validate render node ─────────────────────────────────────────────────────
check_render_node() {
    if [[ ! -e "$RENDER_NODE" ]]; then
        fail "Render node $RENDER_NODE not found. Cannot start Ollama."
    fi
    pass "Render node $RENDER_NODE present."
}

# ─── Main ─────────────────────────────────────────────────────────────────────
main() {
    log "Checking for Intel Arc GPU..."

    local arc_card
    if ! arc_card=$(find_arc_card); then
        fail "No Intel Arc GPU found in /dev/dri/. Check driver installation."
    fi

    log "Detected Intel Arc on: ${arc_card}"

    # Retry loop for signal stability
    local attempt=0
    while (( attempt < MAX_RETRIES )); do
        sleep "$SIGNAL_WAIT"
        if [[ -e "$arc_card" ]] && [[ -e "$RENDER_NODE" ]]; then
            pass "GPU Signal stable on ${arc_card}."
            check_render_node

            # Export for docker-compose (if sourced)
            export GPU_CARD="$arc_card"
            export GPU_RENDER="$RENDER_NODE"

            # Update .env if it exists in the working directory
            if [[ -f ".env" ]]; then
                sed -i "s|^GPU_CARD=.*|GPU_CARD=${arc_card}|" .env
                log "Updated .env with GPU_CARD=${arc_card}"
            fi

            exit 0
        fi
        (( attempt++ ))
        log "Signal check attempt ${attempt}/${MAX_RETRIES} — retrying..."
    done

    fail "GPU signal unstable after ${MAX_RETRIES} attempts. Aborting."
}

main "$@"
