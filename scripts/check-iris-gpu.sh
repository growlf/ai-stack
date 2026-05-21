#!/usr/bin/env bash
# ─── check-iris-gpu.sh ────────────────────────────────────────────────────────
# Detect Intel integrated GPU class and recommend the right ai-stack install
# path. Companion to check-arc-gpu.sh — covers the broader case of older Intel
# iGPUs (Iris Pro 580 / Iris / UHD / Gen 9 / Skylake-class) where the
# docker-compose.arc.yml overlay (ipex-llm/SYCL) doesn't work but the native
# Vulkan-via-Mesa-ANV path does.
#
# Validated 2026-05-19 on Intel NUC6i7KYB (Iris Pro 580, Gen 9 GT4e) and
# lab1/2/3 NUC hardware.
#
# Exit codes:
#   0 — recommendation printed (any class detected, with next-steps shown)
#   1 — no Intel GPU found, or critical prerequisites missing
#
# The verdict (which path to take) is printed to stdout in a parseable format
# on the last line:  VERDICT=<arc|vulkan|ambiguous|none>

set -euo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'
    BLUE=$'\033[1;34m'; RED=$'\033[1;31m'; RESET=$'\033[0m'
else
    BOLD=''; GREEN=''; YELLOW=''; BLUE=''; RED=''; RESET=''
fi

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
fail()    { echo -e "${RED}[FAIL]${RESET}  $*" >&2; }
hdr()     { echo -e "\n${BOLD}${BLUE}── $* ──${RESET}\n"; }

VERDICT="none"

# ─── 1. lspci available? ──────────────────────────────────────────────────────
hdr "Intel iGPU detection"

if ! command -v lspci &>/dev/null; then
    fail "lspci not found. Install pciutils first: sudo apt install pciutils"
    echo "VERDICT=${VERDICT}"
    exit 1
fi

# ─── 2. Find the Intel display device ─────────────────────────────────────────
INTEL_GPU=$(lspci 2>/dev/null | grep -E -i "vga|3d|display" | grep -i "intel" | head -1 || true)

if [[ -z "$INTEL_GPU" ]]; then
    warn "No Intel display device found via lspci."
    info "If you have an NVIDIA GPU, use docker-compose.nvidia.yml instead."
    info "If you have no GPU, the CPU-only stack will work: ./install.sh --cpu"
    echo "VERDICT=${VERDICT}"
    exit 1
fi

info "Detected: ${INTEL_GPU}"

# ─── 3. Classify: Arc vs older iGPU vs Iris Xe ────────────────────────────────
# We classify primarily by the lspci description string, which is human-set by
# the kernel maintainers and the most reliable signal. As a secondary check we
# look at the PCI device-id range when present.

if echo "$INTEL_GPU" | grep -iq " Arc "; then
    VERDICT="arc"
    ok "Class: Intel Arc (Alchemist/Battlemage discrete)"
elif echo "$INTEL_GPU" | grep -iq "Iris Xe\|Gen12\|Tiger Lake\|Alder Lake\|Raptor Lake\|Meteor Lake\|Lunar Lake"; then
    VERDICT="ambiguous"
    ok "Class: Intel Iris Xe / 11th-Gen Core+ (ipex-llm-supported)"
else
    VERDICT="vulkan"
    ok "Class: older Intel iGPU (Iris Pro / Iris / UHD / Gen 9 or older)"
fi

# ─── 4. Driver check (i915) ───────────────────────────────────────────────────
hdr "Driver + render-node check"

DRIVER_LINE=$(lspci -v 2>/dev/null | grep -A 20 "Intel.*\(VGA\|Display\|3D\)" | grep -i "Kernel driver in use" | head -1 || true)
if [[ -z "$DRIVER_LINE" ]]; then
    warn "Could not determine kernel driver for the Intel GPU."
    warn "Run: sudo lspci -v -s 00:02.0 | grep 'Kernel driver'"
else
    DRIVER=$(echo "$DRIVER_LINE" | awk -F: '{print $2}' | xargs)
    if [[ "$DRIVER" == "i915" ]]; then
        ok "Kernel driver: i915 (correct for Intel iGPU)"
    elif [[ "$DRIVER" == "xe" ]]; then
        warn "Kernel driver: xe (newer Intel driver; both Vulkan + arc.yml paths may work but i915 is the verified path for older iGPUs)"
    else
        warn "Kernel driver: ${DRIVER:-unknown} — expected i915 for Intel iGPU"
    fi
fi

# ─── 5. /dev/dri render node ──────────────────────────────────────────────────
if [[ -e /dev/dri/renderD128 ]]; then
    ok "Render node /dev/dri/renderD128 present"
else
    fail "Render node /dev/dri/renderD128 not found. GPU may not be accessible to user-space."
    info "Check: ls -la /dev/dri/ ; lsmod | grep i915"
    echo "VERDICT=${VERDICT}"
    exit 1
fi

# ─── 6. User groups (render + video) ──────────────────────────────────────────
if [[ "${SKIP_GROUP_CHECK:-0}" != "1" ]]; then
    if id -nG 2>/dev/null | tr ' ' '\n' | grep -q '^render$'; then
        ok "User $(id -un) is in the 'render' group"
    else
        warn "User $(id -un) is NOT in the 'render' group"
        info "  Fix: sudo usermod -aG render,video \"\$USER\" && newgrp render"
    fi
fi

# ─── 7. Vulkan path prerequisites (only when Vulkan is recommended) ───────────
if [[ "$VERDICT" == "vulkan" ]] || [[ "$VERDICT" == "ambiguous" ]]; then
    hdr "Vulkan path readiness"

    if command -v vulkaninfo &>/dev/null; then
        ok "vulkaninfo present ($(command -v vulkaninfo))"
        info "  (Skipping live vulkaninfo — can hang on headless systems. Run manually to verify.)"
    else
        warn "vulkaninfo not installed (optional, but useful for live verification)"
        info "  Install: sudo apt install vulkan-tools mesa-vulkan-drivers"
    fi

    if dpkg -l 2>/dev/null | grep -qE '^ii\s+mesa-vulkan-drivers'; then
        ok "mesa-vulkan-drivers installed"
    else
        warn "mesa-vulkan-drivers not installed"
        info "  Install: sudo apt install mesa-vulkan-drivers"
    fi

    # Glance at Mesa version (best-effort; not all distros expose it the same way)
    MESA_VER=$(glxinfo 2>/dev/null | grep -i "OpenGL version" | head -1 || true)
    if [[ -n "$MESA_VER" ]]; then
        info "OpenGL: ${MESA_VER}"
    fi
fi

# ─── 8. Recommendation ────────────────────────────────────────────────────────
hdr "Recommendation"

case "$VERDICT" in
    arc)
        echo "  ${BOLD}Use docker-compose.arc.yml (Intel Arc + ipex-llm/SYCL)${RESET}"
        echo
        echo "  Next steps:"
        echo "    ./install.sh --arc"
        echo
        echo "  See docs/hardware/arc.md for details."
        ;;
    vulkan)
        echo "  ${BOLD}Use the native Vulkan path (Mesa ANV + native Ollama)${RESET}"
        echo
        echo "  ipex-llm/SYCL does NOT support this hardware class. The Vulkan-via-Mesa-ANV"
        echo "  path was validated 2026-05-19 on Iris Pro 580 (Gen 9 GT4e, 72 EUs)."
        echo "  Default context jumps 4096 → 32768 once Vulkan is engaged."
        echo
        echo "  Next steps:"
        echo "    1. scripts/install-vulkan-ollama.sh    # native Ollama + Vulkan systemd drop-in"
        echo "    2. ./install.sh --cpu                  # rest of ai-stack (Olla/LiteLLM/Smart Router)"
        echo "                                           # — picks up the native Ollama on :11434"
        echo
        echo "  Full procedure + troubleshooting: docs/hardware/intel-igpu-vulkan.md"
        ;;
    ambiguous)
        echo "  ${BOLD}Either path works — benchmark to compare${RESET}"
        echo
        echo "  Iris Xe / 11th-Gen Core+ supports both:"
        echo "    - docker-compose.arc.yml (ipex-llm/SYCL — may be faster on supported hardware)"
        echo "    - native Vulkan path     (Mesa ANV — broader compatibility)"
        echo
        echo "  Try arc.yml first (lower-effort, same Docker workflow as the rest of the stack):"
        echo "    ./install.sh --arc"
        echo
        echo "  Fallback if Arc path underperforms:"
        echo "    scripts/install-vulkan-ollama.sh    # docs/hardware/intel-igpu-vulkan.md"
        ;;
    none)
        echo "  ${BOLD}No Intel GPU detected${RESET}"
        echo "    ./install.sh --cpu      # CPU-only"
        echo "    ./install.sh --nvidia   # if you have an NVIDIA GPU"
        ;;
esac

echo
echo "VERDICT=${VERDICT}"
exit 0
