"""Shepherd-node FastAPI app.

Endpoints (v1 scaffold):
  GET  /herd/metrics       — current-state snapshot for the control-plane to poll
  GET  /herd/verify        — raw secondary-source data for cross-source divergence checks
  GET  /herd/schema        — JSON Schema of the metrics document (versioned)
  GET  /herd/capabilities  — orchestrator-facing view of node state
  GET  /herd/history       — SQLite-backed historical query
  POST /herd/events/route  — per-prompt routing event capture from the Router

This scaffolding implements /metrics and /schema; others return stubs to be filled in
as v1 progresses. The shape is committed; the bodies grow.
"""

import asyncio
import os
import socket
import time

from fastapi import FastAPI
from pydantic import BaseModel

from .baseline_check import BASELINE_CHECK_INTERVAL_S, run_baseline_check
from .collectors.olla import collect_olla
from .collectors.ollama import collect_ollama
from .collectors.system import collect_system
from .probes import HardwareMetrics, select_probe
from .snapshot import maybe_write_snapshot, read_latest_snapshot
from .verification import select_verification_probe

SHEPHERD_VERSION = "0.2.0"
NODE_NAME = os.environ.get("SHEPHERD_NODE_NAME", socket.gethostname())
NODE_ADDRESS = os.environ.get("SHEPHERD_NODE_ADDRESS", "")

# Single probe selected at startup; refresh on signal if hardware changes (rare).
_PROBE = select_probe()
_VERIFICATION_PROBE = select_verification_probe(_PROBE.name())
_START_TIME = time.time()

# Layer 4 baseline-check state — most recent result, refreshed every ~5min by
# the background task. Surfaced via /herd/metrics so shepherd-control sees it
# without each /metrics poll triggering a fresh inference.
_latest_baseline: dict = {
    "status": "not_yet_run",
    "message": "Baseline check has not run yet on this shepherd-node",
}


class NodeIdentity(BaseModel):
    name: str
    address: str
    uptime_seconds: int
    shepherd_version: str


class MetricsResponse(BaseModel):
    node: NodeIdentity
    system: dict
    hardware: HardwareMetrics
    ollama: dict
    olla: dict
    baseline: dict


app = FastAPI(title="Shepherd Node", version=SHEPHERD_VERSION)


async def _baseline_check_loop():
    """Periodically run the Layer 4 baseline check + cache the result.

    After each successful check, evaluates Layer 5 snapshot eligibility: when
    Layers 2 + 3 + 4 stay green for KNOWN_GOOD_THRESHOLD_S, snapshot the host's
    known-good state to disk.
    """
    global _latest_baseline
    # Small initial delay so /api/ps has a chance to be populated post-start
    await asyncio.sleep(30)
    while True:
        try:
            _latest_baseline = await run_baseline_check(_PROBE.name())
            print(
                f"[shepherd-node] baseline check → {_latest_baseline.get('status')}: {_latest_baseline.get('message', '')[:200]}"
            )

            # Layer 5: snapshot known-good if all integrity signals are green.
            # Verification probe runs inline here since it's cheap (~tens of ms).
            from .collectors.ollama import collect_ollama_raw

            ollama_raw = await collect_ollama_raw()
            verification_result = await _VERIFICATION_PROBE.verify(ollama_ps_state=ollama_raw)
            ollama_summary = await collect_ollama()
            gpu_warnings = ollama_summary.get("gpu_warnings", []) if isinstance(ollama_summary, dict) else []
            hw = _PROBE.read_metrics()
            await maybe_write_snapshot(
                verification_alive=verification_result.alive,
                baseline_ok=(_latest_baseline.get("status") == "ok"),
                gpu_warnings_count=len(gpu_warnings),
                accelerator_type=_PROBE.name(),
                accelerator_name=hw.accelerator_name,
                baseline_result=_latest_baseline,
                shepherd_version=SHEPHERD_VERSION,
            )
        except Exception as e:
            print(f"[shepherd-node] baseline check error: {type(e).__name__}: {e}")
            _latest_baseline = {
                "status": "error",
                "message": f"baseline check raised: {type(e).__name__}: {e}",
            }
        await asyncio.sleep(BASELINE_CHECK_INTERVAL_S)


@app.on_event("startup")
async def _start_baseline_loop():
    asyncio.create_task(_baseline_check_loop())


@app.get("/herd/metrics", response_model=MetricsResponse)
async def metrics():
    """Current-state snapshot. Polled by the control-plane every ~5s."""
    return MetricsResponse(
        node=NodeIdentity(
            name=NODE_NAME,
            address=NODE_ADDRESS,
            uptime_seconds=int(time.time() - _START_TIME),
            shepherd_version=SHEPHERD_VERSION,
        ),
        system=collect_system(),
        hardware=_PROBE.read_metrics(),
        ollama=await collect_ollama(),
        olla=await collect_olla(),
        baseline=_latest_baseline,
    )


@app.get("/herd/schema")
async def schema(v: int = 1):
    """JSON Schema of the metrics document. Versioned; v1 is current."""
    if v != 1:
        return {"error": f"schema version {v} unknown; v1 is current"}
    return MetricsResponse.model_json_schema()


@app.get("/herd/verify")
async def verify():
    """Cross-source GPU-state verification + raw secondary-source data.

    Returns:
      - verification: VerificationProbe result for the active accelerator
        (NVIDIA implemented; Intel Arc/Iris/AMD/Apple Silicon stubbed). Includes
        alive/divergence_reasons/sources_checked. Layer 3 of the GPU-integrity plan.
      - olla_status_raw: Olla's /internal/status unmodified
      - ollama_ps_raw: Ollama's /api/ps unmodified

    The control-plane polls this endpoint, uses `verification.alive` as the
    headline gate, and surfaces `divergence_reasons` as alerts on the dashboard.
    """
    from .collectors.olla import collect_olla_raw
    from .collectors.ollama import collect_ollama_raw

    ollama_raw = await collect_ollama_raw()
    verification = await _VERIFICATION_PROBE.verify(ollama_ps_state=ollama_raw)

    return {
        "node": NODE_NAME,
        "verification": verification.model_dump(),
        "olla_status_raw": await collect_olla_raw(),
        "ollama_ps_raw": ollama_raw,
    }


@app.get("/herd/capabilities")
async def capabilities():
    """Orchestrator-facing summary: which models are resident, available VRAM, etc.

    Same data as /metrics but filtered for routing-decision usefulness.
    """
    hw = _PROBE.read_metrics()
    vram_free = None
    if hw.vram_used_mb is not None and hw.vram_total_mb is not None:
        vram_free = hw.vram_total_mb - hw.vram_used_mb
    return {
        "node": NODE_NAME,
        "accelerator_type": hw.accelerator_type,
        "accelerator_name": hw.accelerator_name,
        "vram_free_mb": vram_free,
        "resident_models": (await collect_ollama()).get("resident_models", []),
    }


@app.get("/herd/history")
async def history(since: str | None = None, until: str | None = None, limit: int = 100):
    """Time-series query against the local SQLite ring buffer. STUB in v0.1.0 — schema lives in storage/sqlite.py."""
    return {"events": [], "note": "history persistence pending v0.2"}


class RouteEvent(BaseModel):
    timestamp: str
    request_id: str
    prompt_summary: str
    classified_intent: str
    selected_model: str
    selected_node: str
    cold_load: bool
    latency_first_token_ms: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    total_duration_ms: int
    fallback_reason: str | None = None


@app.post("/herd/events/route")
async def post_route_event(event: RouteEvent):
    """Per-prompt routing event from the Router. STUB in v0.1.0 — captures to storage in v0.2."""
    # TODO: persist via storage.sqlite; for now just acknowledge
    return {"received": True, "request_id": event.request_id}


@app.get("/herd/snapshot")
async def snapshot():
    """Latest known-good snapshot for this host (Layer 5).

    Returns the most-recently-written `known-good-<hostname>.json` from
    SHEPHERD_SNAPSHOT_PATH. Snapshots are auto-written when Layers 2 + 3 + 4
    stay green for SHEPHERD_KNOWN_GOOD_THRESHOLD seconds (default 30 min).

    If no snapshot has been written yet, returns 200 with {"snapshot": null}
    rather than 404 — distinguishes "host hasn't reached sustained-green yet"
    from "endpoint doesn't exist."
    """
    return {"snapshot": read_latest_snapshot()}


@app.post("/herd/recover")
async def recover():
    """Trigger probe-specific recovery action.

    Layer 3.5: when shepherd-control's continuous verification detects divergence
    (e.g. NVIDIA's Ollama-vs-host-smi cross-check fires), it POSTs here to attempt
    auto-recovery without operator intervention. The probe owns the action shape
    (NVIDIA → `docker restart ollama`, others → stub no-op for now).

    Authentication is not currently enforced — endpoint is reachable only over
    the BMS LAN + NetBird mesh. Adding auth is a substrate-side ask if this
    becomes a public surface.
    """
    attempt = await _VERIFICATION_PROBE.recover()
    return {
        "node": NODE_NAME,
        "probe": _VERIFICATION_PROBE.name(),
        "attempt": attempt.model_dump(),
    }


@app.get("/herd/healthz")
async def healthz():
    return {"status": "ok", "probe": _PROBE.name(), "uptime_seconds": int(time.time() - _START_TIME)}
