"""Olla collector — queries the local Olla /internal/status for federation state."""

import os
import httpx


OLLA_URL = os.environ.get("OLLA_URL", "http://olla:40114")


async def collect_olla() -> dict:
    """Summarized view of Olla's federation state."""
    raw = await collect_olla_raw()
    if "error" in raw:
        return {"responding": False}
    endpoints = raw.get("endpoints", [])
    healthy_count = sum(1 for e in endpoints if e.get("status") == "healthy")
    litellm = next((e for e in endpoints if "litellm" in (e.get("name", "")).lower()), None)
    return {
        "responding": True,
        "endpoints_healthy": f"{healthy_count}/{len(endpoints)}" if endpoints else "0/0",
        "litellm_cloud_status": litellm.get("status") if litellm else "absent",
        "active_connections": raw.get("system", {}).get("active_connections", 0),
    }


async def collect_olla_raw() -> dict:
    """Unmodified Olla /internal/status for cross-source verification."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{OLLA_URL}/internal/status")
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return {"error": str(e)}
