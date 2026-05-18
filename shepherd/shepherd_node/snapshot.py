"""Snapshot known-good state — Layer 5 of the GPU-integrity plan.

When all three Layers (2 + 3 + 4) report green for sustained time (KNOWN_GOOD_THRESHOLD_S),
capture a `known-good-baseline.json` for this host. The artifact is what we restore
*from* when things drift — replaces the "go read Garth's Obsidian journal to find out
what worked April 6" pattern that bit us with Phoenix Arc.

Captured snapshot fields:
  - timestamp (ISO-8601 UTC)
  - hostname + accelerator_type
  - kernel version (uname -r)
  - driver / vendor SMI output (nvidia-smi -L, intel_gpu_top, rocm-smi as applicable)
  - container image digests (when shepherd-node or Ollama is containerized)
  - latest baseline_check tok/s + model
  - hardware metrics (vram total, accelerator name from probe)
  - shepherd version

The snapshot persists to ${SHEPHERD_SNAPSHOT_PATH}/known-good-<hostname>.json on the
local filesystem. Exposed via GET /herd/snapshot so shepherd-control and operators
can read it without SSHing.

Sustained-green semantics:
  - Track timestamp of last "green sweep" (Layer 3 alive + Layer 4 ok)
  - When sustained_green_seconds >= KNOWN_GOOD_THRESHOLD_S, write/refresh snapshot
  - On any non-green signal, reset sustained-green timer (so we never snapshot
    a recently-degraded state)

Configurable via SHEPHERD_KNOWN_GOOD_THRESHOLD env (default 1800s = 30 min) and
SHEPHERD_SNAPSHOT_PATH env (default $HOME/.shepherd/).
"""

import asyncio
import json
import os
import shutil
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

KNOWN_GOOD_THRESHOLD_S = int(os.environ.get("SHEPHERD_KNOWN_GOOD_THRESHOLD", "1800"))
SNAPSHOT_PATH = Path(os.environ.get("SHEPHERD_SNAPSHOT_PATH", str(Path.home() / ".shepherd")))


# Module-level state: when did the green-streak start?
# None = not currently green; epoch seconds = green since this time.
_green_streak_started_at: float | None = None
_latest_snapshot_written_at: float | None = None


async def _run_capture(cmd: list[str], timeout: float = 5.0) -> str:
    """Run a shell command and return stdout, or empty string on failure."""
    if not cmd or not shutil.which(cmd[0]):
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
        return ""
    except (TimeoutError, FileNotFoundError, OSError):
        return ""


async def collect_snapshot(
    accelerator_type: str,
    accelerator_name: str,
    baseline_result: dict,
    shepherd_version: str,
) -> dict:
    """Build the known-good snapshot dict for this host."""
    kernel = await _run_capture(["uname", "-r"])
    accel_smi = ""
    if accelerator_type == "nvidia":
        accel_smi = await _run_capture(["nvidia-smi", "-L"])
    elif accelerator_type in ("intel-arc", "intel-iris"):
        # `intel_gpu_top -L` lists devices; if that's not present, try sycl-ls
        accel_smi = await _run_capture(["intel_gpu_top", "-L"])
        if not accel_smi:
            accel_smi = await _run_capture(["sycl-ls"])
    elif accelerator_type == "amd":
        accel_smi = await _run_capture(["rocm-smi", "-i"])
    elif accelerator_type == "apple-silicon":
        accel_smi = await _run_capture(["system_profiler", "SPDisplaysDataType"])

    # Container image digests for any running Ollama-like container
    container_info = await _run_capture(
        [
            "docker",
            "ps",
            "--filter",
            "name=ollama",
            "--format",
            "{{.Names}}|{{.Image}}|{{.ID}}",
        ]
    )

    return {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "hostname": socket.gethostname(),
        "accelerator": {
            "type": accelerator_type,
            "name": accelerator_name,
            "smi_output": accel_smi[:2000],  # cap for sanity
        },
        "kernel": kernel,
        "shepherd_version": shepherd_version,
        "baseline": baseline_result,
        "containers": container_info,
        "note": (
            "This snapshot was captured automatically when Layers 2 + 3 + 4 reported "
            "green for the configured KNOWN_GOOD_THRESHOLD. Restore from this state "
            "when things drift — kernel/driver versions here are what was working."
        ),
    }


async def maybe_write_snapshot(
    verification_alive: bool,
    baseline_ok: bool,
    gpu_warnings_count: int,
    accelerator_type: str,
    accelerator_name: str,
    baseline_result: dict,
    shepherd_version: str,
) -> dict | None:
    """Track the green-streak; if it crosses the threshold, write a fresh snapshot.

    Returns the snapshot dict if one was just written, else None.
    """
    global _green_streak_started_at, _latest_snapshot_written_at

    all_green = verification_alive and baseline_ok and gpu_warnings_count == 0

    if not all_green:
        # Reset the streak — we don't snapshot recently-degraded state.
        if _green_streak_started_at is not None:
            print(
                f"[shepherd-node] snapshot: green streak broken (verify={verification_alive} baseline_ok={baseline_ok} warnings={gpu_warnings_count})"
            )
        _green_streak_started_at = None
        return None

    now = time.time()
    if _green_streak_started_at is None:
        _green_streak_started_at = now
        print(
            f"[shepherd-node] snapshot: green streak started at t={int(now)}; will snapshot if sustained {KNOWN_GOOD_THRESHOLD_S}s"
        )
        return None

    sustained = now - _green_streak_started_at
    if sustained < KNOWN_GOOD_THRESHOLD_S:
        return None

    # Avoid hammer-writing: if we already wrote within the last threshold window, skip
    if _latest_snapshot_written_at and (now - _latest_snapshot_written_at) < KNOWN_GOOD_THRESHOLD_S:
        return None

    snapshot = await collect_snapshot(accelerator_type, accelerator_name, baseline_result, shepherd_version)

    try:
        SNAPSHOT_PATH.mkdir(parents=True, exist_ok=True)
        out_path = SNAPSHOT_PATH / f"known-good-{socket.gethostname()}.json"
        out_path.write_text(json.dumps(snapshot, indent=2))
        _latest_snapshot_written_at = now
        print(
            f"[shepherd-node] snapshot: wrote known-good state to {out_path} (sustained {int(sustained)}s green)"
        )
    except OSError as e:
        print(f"[shepherd-node] snapshot: write failed: {type(e).__name__}: {e}")
        return None

    return snapshot


def read_latest_snapshot() -> dict | None:
    """Read the most-recently-written known-good snapshot for this host."""
    out_path = SNAPSHOT_PATH / f"known-good-{socket.gethostname()}.json"
    if not out_path.exists():
        return None
    try:
        return json.loads(out_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
