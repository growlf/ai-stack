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

import os
import socket
import time

import psutil
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

from .probes import select_probe, HardwareMetrics
from .collectors.system import collect_system
from .collectors.ollama import collect_ollama
from .collectors.olla import collect_olla


SHEPHERD_VERSION = "0.1.0"
NODE_NAME = os.environ.get("SHEPHERD_NODE_NAME", socket.gethostname())
NODE_ADDRESS = os.environ.get("SHEPHERD_NODE_ADDRESS", "")

# Single probe selected at startup; refresh on signal if hardware changes (rare).
_PROBE = select_probe()
_START_TIME = time.time()


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


app = FastAPI(title="Shepherd Node", version=SHEPHERD_VERSION)


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
    )


@app.get("/herd/schema")
async def schema(v: int = 1):
    """JSON Schema of the metrics document. Versioned; v1 is current."""
    if v != 1:
        return {"error": f"schema version {v} unknown; v1 is current"}
    return MetricsResponse.model_json_schema()


@app.get("/herd/verify")
async def verify():
    """Raw secondary-source data for cross-source divergence detection.

    Returns Olla's /internal/status AND Ollama's /api/ps unmodified, so the control-plane
    can compare them and surface divergences (the Apostle failure class — "Olla says
    healthy but Ollama says size_vram=0" gets caught here).
    """
    from .collectors.olla import collect_olla_raw
    from .collectors.ollama import collect_ollama_raw
    return {
        "node": NODE_NAME,
        "olla_status_raw": await collect_olla_raw(),
        "ollama_ps_raw": await collect_ollama_raw(),
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
async def history(since: Optional[str] = None, until: Optional[str] = None, limit: int = 100):
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
    latency_first_token_ms: Optional[int] = None
    tokens_in: int = 0
    tokens_out: int = 0
    total_duration_ms: int
    fallback_reason: Optional[str] = None


@app.post("/herd/events/route")
async def post_route_event(event: RouteEvent):
    """Per-prompt routing event from the Router. STUB in v0.1.0 — captures to storage in v0.2."""
    # TODO: persist via storage.sqlite; for now just acknowledge
    return {"received": True, "request_id": event.request_id}


@app.get("/herd/healthz")
async def healthz():
    return {"status": "ok", "probe": _PROBE.name(), "uptime_seconds": int(time.time() - _START_TIME)}
