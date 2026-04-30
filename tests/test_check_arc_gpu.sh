#!/usr/bin/env bash
# ─── tests/test_check_arc_gpu.sh ─────────────────────────────────────────────
# Lightweight unit-test harness for scripts/check-arc-gpu.sh.
#
# No external framework required — pure bash.
#
# Usage:
#   bash tests/test_check_arc_gpu.sh
#
# Exit codes:
#   0 — all tests passed
#   1 — one or more tests failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SUBJECT="${REPO_ROOT}/scripts/check-arc-gpu.sh"

# ─── Minimal test harness ────────────────────────────────────────────────────
PASS=0; FAIL=0

pass() { echo "[PASS] $1"; (( PASS++ )) || true; }
fail() { echo "[FAIL] $1"; (( FAIL++ )) || true; }

assert_exit() {
    local description="$1"
    local expected_exit="$2"
    local actual_exit="$3"
    if [[ "$actual_exit" -eq "$expected_exit" ]]; then
        pass "$description"
    else
        fail "$description (expected exit $expected_exit, got $actual_exit)"
    fi
}

assert_output_contains() {
    local description="$1"
    local pattern="$2"
    local output="$3"
    if echo "$output" | grep -qE "$pattern"; then
        pass "$description"
    else
        fail "$description (pattern '$pattern' not found in output)"
    fi
}

assert_output_not_contains() {
    local description="$1"
    local pattern="$2"
    local output="$3"
    if echo "$output" | grep -qE "$pattern"; then
        fail "$description (unexpected pattern '$pattern' found in output)"
    else
        pass "$description"
    fi
}

# ─── Test helpers ────────────────────────────────────────────────────────────

# Run the script inside a temporary fake /dev/dri environment.
# Arguments:
#   $1  vendor string (e.g. "0x8086") or "" to skip vendor file creation
#   $2  create render node? ("yes"/"no")
# Returns exit code and stdout/stderr in $RUN_OUTPUT.
run_with_fake_gpu() {
    local vendor="$1"
    local create_render="$2"

    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' RETURN

    # Build fake /dev/dri tree
    local fake_dri="${tmpdir}/dev/dri"
    mkdir -p "${fake_dri}"
    touch "${fake_dri}/card0"

    # Build fake sysfs vendor entry
    local fake_sysfs="${tmpdir}/sys/class/drm/card0/device"
    mkdir -p "${fake_sysfs}"
    if [[ -n "$vendor" ]]; then
        echo "$vendor" > "${fake_sysfs}/vendor"
    fi

    # Optionally create render node
    if [[ "$create_render" == "yes" ]]; then
        touch "${fake_dri}/renderD128"
    fi

    # Patch the script: replace hard-coded /dev/dri and /sys/class/drm paths
    # with our temporary paths, and remove the sleep to keep tests fast.
    local patched="${tmpdir}/check-arc-gpu-patched.sh"
    sed \
        -e "s|/dev/dri|${fake_dri}|g" \
        -e "s|/sys/class/drm|${tmpdir}/sys/class/drm|g" \
        -e "s|SIGNAL_WAIT=2|SIGNAL_WAIT=0|g" \
        -e "s|MAX_RETRIES=3|MAX_RETRIES=1|g" \
        "$SUBJECT" > "$patched"
    chmod +x "$patched"

    RUN_OUTPUT=$(bash "$patched" 2>&1)
    return $?
}

# ─── Tests ───────────────────────────────────────────────────────────────────

# 1. Intel GPU found, render node present → success
run_with_fake_gpu "0x8086" "yes"
assert_exit \
    "Intel GPU + render node: exits 0" \
    0 $?
assert_output_contains \
    "Intel GPU + render node: reports stable signal" \
    "GPU Signal stable" \
    "$RUN_OUTPUT"
assert_output_contains \
    "Intel GPU + render node: reports render node present" \
    "Render node.*present" \
    "$RUN_OUTPUT"

# 2. Non-Intel GPU (AMD) → exits 1
run_with_fake_gpu "0x1002" "yes"
_exit=$?
assert_exit \
    "AMD GPU: exits 1" \
    1 $_exit
assert_output_contains \
    "AMD GPU: reports no Intel Arc found" \
    "No Intel Arc GPU found" \
    "$RUN_OUTPUT"

# 3. No vendor file at all → exits 1
run_with_fake_gpu "" "yes"
_exit=$?
assert_exit \
    "No vendor file: exits 1" \
    1 $_exit

# 4. Intel GPU but no render node → exits 1
run_with_fake_gpu "0x8086" "no"
_exit=$?
assert_exit \
    "Intel GPU, missing render node: exits 1" \
    1 $_exit
assert_output_not_contains \
    "Intel GPU, missing render node: does not report success" \
    "GPU Signal stable|Render node.*present" \
    "$RUN_OUTPUT"

# 5. Checking signal stability message appears
run_with_fake_gpu "0x8086" "yes"
assert_output_contains \
    "Startup: logs Arc detection message" \
    "Checking for Intel Arc GPU" \
    "$RUN_OUTPUT"

# 6. Output includes detected card node path
run_with_fake_gpu "0x8086" "yes"
assert_output_contains \
    "Output: detected card node path is shown" \
    "Detected Intel Arc on:.*card0" \
    "$RUN_OUTPUT"

# 7. GPU_CARD exported on success (subshell prints it before exit)
tmpdir=$(mktemp -d)
fake_dri="${tmpdir}/dev/dri"
mkdir -p "${fake_dri}"
touch "${fake_dri}/card0"
fake_sysfs="${tmpdir}/sys/class/drm/card0/device"
mkdir -p "${fake_sysfs}"
echo "0x8086" > "${fake_sysfs}/vendor"
touch "${fake_dri}/renderD128"

patched="${tmpdir}/check-arc-gpu-patched.sh"
sed \
    -e "s|/dev/dri|${fake_dri}|g" \
    -e "s|/sys/class/drm|${tmpdir}/sys/class/drm|g" \
    -e "s|SIGNAL_WAIT=2|SIGNAL_WAIT=0|g" \
    -e "s|MAX_RETRIES=3|MAX_RETRIES=1|g" \
    "$SUBJECT" > "$patched"
chmod +x "$patched"

# Add a line that prints GPU_CARD just before the exit 0, to verify it was exported
sed -i 's/exit 0/echo "EXPORTED_GPU_CARD=${GPU_CARD}"; exit 0/' "$patched"
_export_check=$(bash "$patched" 2>&1)
if echo "$_export_check" | grep -qE "EXPORTED_GPU_CARD=.+card"; then
    pass "GPU_CARD is exported and set to the detected card path"
else
    fail "GPU_CARD was not exported correctly (output: $_export_check)"
fi
rm -rf "$tmpdir"

# 8. .env file updated when present
tmpdir=$(mktemp -d)
fake_dri="${tmpdir}/dev/dri"
mkdir -p "${fake_dri}"
touch "${fake_dri}/card0"
fake_sysfs="${tmpdir}/sys/class/drm/card0/device"
mkdir -p "${fake_sysfs}"
echo "0x8086" > "${fake_sysfs}/vendor"
touch "${fake_dri}/renderD128"

patched="${tmpdir}/check-arc-gpu-patched.sh"
sed \
    -e "s|/dev/dri|${fake_dri}|g" \
    -e "s|/sys/class/drm|${tmpdir}/sys/class/drm|g" \
    -e "s|SIGNAL_WAIT=2|SIGNAL_WAIT=0|g" \
    -e "s|MAX_RETRIES=3|MAX_RETRIES=1|g" \
    "$SUBJECT" > "$patched"
chmod +x "$patched"

# Create a .env in the working directory used by the patched script
echo "GPU_CARD=/dev/dri/card9" > "${tmpdir}/.env"
( cd "$tmpdir" && bash "$patched" >/dev/null 2>&1 ) || true
if grep -qE "GPU_CARD=.*/card0" "${tmpdir}/.env"; then
    pass ".env GPU_CARD updated to detected card"
else
    fail ".env GPU_CARD not updated (content: $(cat "${tmpdir}/.env"))"
fi
rm -rf "$tmpdir"

# 9. Script is executable and has proper shebang
if [[ -x "$SUBJECT" ]]; then
    pass "Script is executable"
else
    fail "Script is not executable"
fi

first_line=$(head -n 1 "$SUBJECT")
if [[ "$first_line" == "#!/usr/bin/env bash" ]]; then
    pass "Script has correct shebang"
else
    fail "Script shebang is '$first_line', expected '#!/usr/bin/env bash'"
fi

# 10. Script uses set -euo pipefail
if grep -q "set -euo pipefail" "$SUBJECT"; then
    pass "Script uses 'set -euo pipefail'"
else
    fail "Script does not use 'set -euo pipefail'"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
if (( FAIL > 0 )); then
    exit 1
fi
exit 0
