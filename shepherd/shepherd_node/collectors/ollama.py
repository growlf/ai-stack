"""Ollama collector — queries the local Ollama /api/ps for resident model state.

Includes a lightweight divergence-detection layer (Layer 2 of the GPU-integrity plan):
when a model that previously reported size_vram > 0 now reports size_vram == 0 *while
remaining in /api/ps*, that indicates Ollama believes the model is loaded but has lost
GPU allocation — i.e. silent CPU fallback. The 2026-05-16 cluster-llm regression
showed exactly this pattern (Ollama claimed size_vram=8GB while nvidia-smi showed 0 MiB
used). Graceful unload (model leaves /api/ps) does *not* fire the warning; only the
"stayed loaded but lost VRAM" case is treated as divergence.
"""

import os
import httpx


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")


# Module-level state: last observed VRAM per model. Process-scoped — resets on
# shepherd-node restart. Restart re-establishes baseline on first poll.
_last_vram_mb_by_model: dict[str, int] = {}


async def collect_ollama() -> dict:
    """Summarized view of resident models, with silent-CPU-fallback divergence warnings."""
    raw = await collect_ollama_raw()
    if "error" in raw:
        return {"responding": False, "resident_models": [], "gpu_warnings": []}

    current_models = [
        {
            "name": m.get("name"),
            "size_vram_mb": (m.get("size_vram", 0) or 0) // (1024 * 1024),
            "size_total_mb": (m.get("size", 0) or 0) // (1024 * 1024),
            "expires_at": m.get("expires_at"),
        }
        for m in raw.get("models", [])
    ]
    current_names = {m["name"] for m in current_models if m["name"]}

    gpu_warnings = []
    for m in current_models:
        name = m["name"]
        if not name:
            continue
        current = m["size_vram_mb"]
        previous = _last_vram_mb_by_model.get(name, 0)
        # Drop-to-zero on a still-loaded model = silent CPU fallback signal
        if previous > 0 and current == 0:
            gpu_warnings.append({
                "model": name,
                "kind": "size_vram_drop_to_zero",
                "previous_vram_mb": previous,
                "current_vram_mb": 0,
                "message": (
                    f"Model {name} was {previous} MB in VRAM, now reports 0 MB. "
                    "Ollama still has the model in /api/ps — likely silent CPU fallback "
                    "(NVML handle drift, container GPU passthrough lost, or similar). "
                    "Verify with host nvidia-smi / intel_gpu_top."
                ),
            })
        _last_vram_mb_by_model[name] = current

    # Forget state for models no longer in /api/ps (graceful unload, not a warning case)
    for stale in [n for n in _last_vram_mb_by_model if n not in current_names]:
        del _last_vram_mb_by_model[stale]

    return {
        "responding": True,
        "resident_models": current_models,
        "gpu_warnings": gpu_warnings,
    }


async def collect_ollama_raw() -> dict:
    """Unmodified Ollama /api/ps for cross-source verification."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/ps")
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return {"error": str(e)}
