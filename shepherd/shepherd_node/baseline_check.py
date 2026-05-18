"""Baseline-check collector — Layer 4 of the GPU-integrity plan.

Periodically runs a short synthetic prompt against a currently-resident model
and measures real-world tokens-per-second. Compares the measurement against a
tier-floor baseline in `gpu-baselines.json`. Below-floor = latency regression
(GPU nominally working per Layer 3, but performance has degraded).

Different threat model from Layer 3:
  - Layer 3 catches: 'NVML drift / container lost GPU passthrough' (Ollama
    claims VRAM but host_smi disagrees — fundamental access broken)
  - Layer 4 catches: 'GPU accessible but unexpectedly slow' (thermal throttle,
    competing workload, model loaded with wrong layer count, partial CPU
    offload, etc. — degradation, not outage)

Cadence: every BASELINE_CHECK_INTERVAL_S seconds (default 300 = 5 min). The
synthetic prompt is short ("say hi") + low-output (max ~10 tokens) so the
check itself is sub-second on a healthy GPU. On a degraded GPU it'll take
longer — that's precisely what we're measuring.

If no model is resident, skip cleanly. If Ollama is down, skip cleanly. If
the resident model has no baseline entry, report 'no_baseline' rather than
failing.
"""

import json
import os
import time
from pathlib import Path

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
BASELINE_CHECK_INTERVAL_S = int(os.environ.get("SHEPHERD_BASELINE_INTERVAL", "300"))
BASELINES_PATH = Path(__file__).resolve().parent.parent / "gpu-baselines.json"

# Synthetic prompt: short to keep the check sub-second; deterministic to keep
# measurements comparable across runs.
SYNTHETIC_PROMPT = "Reply with exactly: hi"
SYNTHETIC_MAX_TOKENS = 12


_baselines_cache: dict | None = None


def _load_baselines() -> dict:
    global _baselines_cache
    if _baselines_cache is not None:
        return _baselines_cache
    try:
        with open(BASELINES_PATH) as f:
            _baselines_cache = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _baselines_cache = {"version": 0, "tiers": {}, "_load_error": str(e)}
    return _baselines_cache


def _lookup_floor(accelerator_type: str, model_name: str) -> dict | None:
    """Returns the baseline entry for (accelerator, model) or None if not found."""
    baselines = _load_baselines()
    tier = baselines.get("tiers", {}).get(accelerator_type, {})
    return tier.get(model_name)


async def run_baseline_check(accelerator_type: str) -> dict:
    """One-shot baseline check. Returns a result dict.

    Schema:
        status: "ok" | "regression" | "no_baseline" | "no_model" | "ollama_down"
        model: the model tested (if any)
        measured_tok_s: int (or null)
        baseline_tok_s_floor: int (or null)
        baseline_tok_s_typical: int (or null)
        elapsed_ms: int (total request duration)
        message: human-readable summary
    """
    # Find a resident model to test against
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/ps")
            r.raise_for_status()
            ps = r.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return {
            "status": "ollama_down",
            "model": None,
            "measured_tok_s": None,
            "message": f"Ollama /api/ps unreachable: {type(e).__name__}",
        }

    models = ps.get("models", []) or []
    if not models:
        return {
            "status": "no_model",
            "model": None,
            "measured_tok_s": None,
            "message": "No resident model in /api/ps; skipping baseline check.",
        }

    # Prefer a model that has a baseline entry; fall back to the first resident
    chosen = None
    for m in models:
        name = m.get("name") or m.get("model")
        if name and _lookup_floor(accelerator_type, name):
            chosen = name
            break
    if chosen is None:
        chosen = models[0].get("name") or models[0].get("model")

    # Run the synthetic inference + measure tok/s
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": chosen,
                    "messages": [{"role": "user", "content": SYNTHETIC_PROMPT}],
                    "stream": False,
                    "options": {"num_predict": SYNTHETIC_MAX_TOKENS},
                },
            )
            r.raise_for_status()
            data = r.json()
        elapsed_ms = int((time.monotonic() - start) * 1000)
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return {
            "status": "ollama_down",
            "model": chosen,
            "measured_tok_s": None,
            "message": f"Synthetic prompt failed: {type(e).__name__}: {e}",
        }

    # Compute tok/s from Ollama's own timing (more accurate than wall-clock)
    eval_count = data.get("eval_count", 0) or 0
    eval_duration_ns = data.get("eval_duration", 0) or 0
    if eval_duration_ns <= 0 or eval_count <= 0:
        # Fall back to wall-clock if Ollama didn't report timings
        if elapsed_ms <= 0:
            measured_tok_s = None
        else:
            # We don't know actual tokens generated; approximate as max_tokens
            measured_tok_s = round((eval_count or SYNTHETIC_MAX_TOKENS) / (elapsed_ms / 1000))
    else:
        measured_tok_s = round(eval_count / (eval_duration_ns / 1e9))

    baseline = _lookup_floor(accelerator_type, chosen)
    if baseline is None:
        return {
            "status": "no_baseline",
            "model": chosen,
            "measured_tok_s": measured_tok_s,
            "elapsed_ms": elapsed_ms,
            "message": (
                f"Resident model '{chosen}' has no baseline entry for "
                f"accelerator '{accelerator_type}' in gpu-baselines.json. "
                f"Measured {measured_tok_s} tok/s."
            ),
        }

    floor = baseline.get("tok_s_floor")
    typical = baseline.get("tok_s_typical")

    if floor is not None and measured_tok_s is not None and measured_tok_s < floor:
        return {
            "status": "regression",
            "model": chosen,
            "measured_tok_s": measured_tok_s,
            "baseline_tok_s_floor": floor,
            "baseline_tok_s_typical": typical,
            "elapsed_ms": elapsed_ms,
            "message": (
                f"Latency regression: {chosen} measured {measured_tok_s} tok/s "
                f"vs floor {floor} tok/s (typical {typical}). GPU may be thermal-"
                f"throttled, sharing with another workload, or loaded with wrong "
                f"layer count. Cross-check with Layer 3 verification + nvidia-smi."
            ),
        }

    return {
        "status": "ok",
        "model": chosen,
        "measured_tok_s": measured_tok_s,
        "baseline_tok_s_floor": floor,
        "baseline_tok_s_typical": typical,
        "elapsed_ms": elapsed_ms,
        "message": f"{chosen}: {measured_tok_s} tok/s (floor {floor}, typical {typical})",
    }
