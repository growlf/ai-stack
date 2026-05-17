"""Ollama collector — queries the local Ollama /api/ps for resident model state."""

import os
import httpx


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")


async def collect_ollama() -> dict:
    """Summarized view of resident models."""
    raw = await collect_ollama_raw()
    if "error" in raw:
        return {"responding": False, "resident_models": []}
    return {
        "responding": True,
        "resident_models": [
            {
                "name": m.get("name"),
                "size_vram_mb": (m.get("size_vram", 0) or 0) // (1024 * 1024),
                "size_total_mb": (m.get("size", 0) or 0) // (1024 * 1024),
                "expires_at": m.get("expires_at"),
            }
            for m in raw.get("models", [])
        ],
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
