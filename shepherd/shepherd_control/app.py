"""Shepherd control-plane — polls peer shepherd-node services + Olla federation view, aggregates, serves UI.

v0.1.1: Added lite node cards synthesized from Olla's /internal/status so all BMS herd
peers appear in the dashboard, even peers without a deployed shepherd-node yet. Full
data (system metrics, GPU details) requires a real shepherd-node on that peer; lite
cards show what Olla can see.
"""

import asyncio
import os
import socket
import time
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles


# Human-friendly name for the host running shepherd-control. Used to substitute
# "localhost" / "127.0.0.1" in discovered_via attribution so the dashboard says
# "via cluster-llm" instead of the confusing "via localhost".
SHEPHERD_CONTROL_NAME = os.environ.get("SHEPHERD_CONTROL_NAME", socket.gethostname())


def _normalize_discovery_source(olla_url: str) -> str:
    """Convert localhost/127.0.0.1 Olla URLs to the control host's display name."""
    lower = olla_url.lower()
    if "localhost" in lower or "127.0.0.1" in lower:
        return SHEPHERD_CONTROL_NAME
    # External URL — strip scheme + port for display
    cleaned = olla_url.split("://", 1)[-1]
    return cleaned.split(":")[0] or olla_url


# Peer list: shepherd-node peers (full data)
PEERS_RAW = os.environ.get(
    "SHEPHERD_PEERS",
    "cluster-llm=http://localhost:40118",
)
PEERS: list[tuple[str, str]] = []
for entry in PEERS_RAW.split(","):
    entry = entry.strip()
    if not entry:
        continue
    if "=" in entry:
        name, url = entry.split("=", 1)
    else:
        name, url = entry, entry
    PEERS.append((name.strip(), url.strip()))


# Olla URLs — for the federation-view lite peers. Comma-separated; both queried and merged.
# Useful when cluster-llm's Olla has a small peer list but Phoenix's Olla sees the wider BMS LAN.
OLLA_URLS_RAW = os.environ.get("OLLA_URLS", os.environ.get("OLLA_URL", "http://localhost:40114"))
OLLA_URLS: list[str] = [u.strip() for u in OLLA_URLS_RAW.split(",") if u.strip()]

# Canonical-name aliasing: maps Olla endpoint names (which differ between Ollas — cluster-llm
# uses "1" for nuk1 via OLLAMA_REMOTE_1, Phoenix uses "10_10_0_215" for the same node) to
# stable display names. Endpoints aliased to a real shepherd-node peer name get filtered out
# (avoids cluster-llm appearing twice — once as shepherd-node, once as "10_10_0_201" from
# Phoenix's POV).
CANONICAL_NAMES = {
    "10_10_0_201": "cluster-llm",  # cluster-llm's LAN IP
    "10_10_0_215": "nuk1",
    "1": "nuk1",  # cluster-llm's OLLAMA_REMOTE_1 alias for nuk1
    "10_10_0_211": "lab1",
    "10_10_0_212": "lab2",
    "10_10_0_213": "lab3",
    "10_10_0_214": "lab4",
}


_snapshot: dict = {"nodes": [], "timestamp": None, "source": "starting"}
_poll_interval_s = int(os.environ.get("SHEPHERD_POLL_INTERVAL", "5"))


# Layer 3.5 auto-recovery state: track recovery attempts per peer with circuit breaker.
# Shape: {peer_name: {"attempts": int, "last_attempt_at": float, "last_action": str}}
# Reset when verification.alive returns True. Stops trying after MAX_RECOVERY_ATTEMPTS
# until either alive=True returns OR the circuit is manually reset.
_recovery_state: dict[str, dict] = {}
MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_COOLDOWN_S = 60  # min seconds between attempts; let restart complete + warm up
RECOVERY_ENABLED = os.environ.get("SHEPHERD_AUTO_RECOVERY", "1") not in ("0", "false", "False")


async def maybe_recover_peer(peer: dict):
    """Layer 3.5: if verification shows divergence and circuit is still closed, POST /herd/recover.

    Idempotent across poll cycles via the _recovery_state dict. On success
    (verification returns alive=True later), the state resets so future divergences
    get fresh attempt budgets. After MAX_RECOVERY_ATTEMPTS without success, stops
    trying — manual operator action needed.
    """
    if not RECOVERY_ENABLED:
        return
    name = peer.get("name")
    if not name or not peer.get("reachable"):
        return
    verification = peer.get("verification")
    if not verification:
        return  # peer doesn't expose verification (older shepherd-node)

    # Recovery succeeded — reset circuit
    if verification.get("alive") is True:
        if name in _recovery_state:
            print(f"[shepherd-control] {name}: verification alive=True, resetting recovery state")
            del _recovery_state[name]
        return

    # Divergence detected — check circuit
    state = _recovery_state.setdefault(name, {"attempts": 0, "last_attempt_at": 0.0, "last_action": ""})
    now = time.time()
    if state["attempts"] >= MAX_RECOVERY_ATTEMPTS:
        return  # circuit open; manual intervention required
    if now - state["last_attempt_at"] < RECOVERY_COOLDOWN_S:
        return  # cooldown still active

    url = peer.get("address")
    if not url:
        return
    state["attempts"] += 1
    state["last_attempt_at"] = now
    print(f"[shepherd-control] {name}: attempting auto-recovery {state['attempts']}/{MAX_RECOVERY_ATTEMPTS}")
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(f"{url}/herd/recover")
            r.raise_for_status()
            result = r.json()
            attempt = result.get("attempt", {})
            state["last_action"] = attempt.get("action", "")
            success = attempt.get("success")
            msg = attempt.get("message", "")[:200]
            print(f"[shepherd-control] {name}: recovery attempt → success={success} action={state['last_action']} msg={msg}")
    except Exception as e:
        print(f"[shepherd-control] {name}: recovery POST failed: {type(e).__name__}: {e}")


async def poll_one_peer(name: str, url: str) -> dict:
    """Pull /herd/metrics + /herd/verify from one real shepherd-node peer.

    /herd/metrics gives the standard current-state snapshot.
    /herd/verify gives the cross-source verification result (Layer 3) — alive/divergence
    signal. Surfaced as a top-level `verification` field on the peer dict so the
    dashboard can render divergence alerts independently of the metrics card.
    Verify is best-effort: if it fails (older shepherd-node not yet upgraded), peer
    stays reachable with verification=None.
    """
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{url}/herd/metrics")
            r.raise_for_status()
            metrics = r.json()

            # Best-effort verify poll — older shepherd-node versions may not have the
            # updated /herd/verify shape with `verification` field. Fall back gracefully.
            verification = None
            try:
                vr = await client.get(f"{url}/herd/verify")
                if vr.status_code == 200:
                    verify_data = vr.json()
                    verification = verify_data.get("verification")
            except Exception:
                pass

            return {
                "name": metrics.get("node", {}).get("name") or name,
                "address": url,
                "role": "herd peer (full)",
                "reachable": True,
                "data_quality": "full",  # shepherd-node provides everything
                "hardware": metrics.get("hardware"),
                "system": metrics.get("system"),
                "ollama": metrics.get("ollama"),
                "olla": metrics.get("olla"),
                "verification": verification,
                "shepherd_version": metrics.get("node", {}).get("shepherd_version"),
            }
    except Exception as e:
        return {
            "name": name,
            "address": url,
            "role": "herd peer (full)",
            "reachable": False,
            "data_quality": "unreachable",
            "error": str(e),
            "hardware": None,
            "system": None,
            "ollama": None,
            "olla": None,
            "verification": None,
        }


async def fetch_remote_ollama_models(remote_url: str) -> list[dict]:
    """Try to query a remote Ollama's /api/ps directly for resident models.

    Returns empty list on failure. Lite peers may not be directly reachable from
    shepherd-control; this is best-effort. If it fails, we fall back to Olla's
    model_discovery count without the model names.
    """
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{remote_url}/api/ps")
            r.raise_for_status()
            return [
                {
                    "name": m.get("name"),
                    "size_vram_mb": (m.get("size_vram", 0) or 0) // (1024 * 1024),
                    "size_total_mb": (m.get("size", 0) or 0) // (1024 * 1024),
                    "expires_at": m.get("expires_at"),
                }
                for m in r.json().get("models", [])
            ]
    except Exception:
        return []


async def derive_olla_peers() -> list[dict]:
    """Use each configured Olla's /internal/status to synthesize lite node cards for
    every federated endpoint not already a real shepherd-node peer. Deduplicates by URL."""
    real_peer_names = {n for n, _ in PEERS}
    seen_endpoint_names: set[str] = set()  # dedup by name (Olla doesn't surface URLs in /internal/status)
    lite_peers: list[dict] = []

    for olla_url in OLLA_URLS:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.get(f"{olla_url}/internal/status")
                r.raise_for_status()
                data = r.json()
            print(f"[shepherd-control] queried {olla_url} → {len(data.get('endpoints', []))} endpoints")
        except Exception as e:
            print(f"[shepherd-control] ERROR querying {olla_url}: {type(e).__name__}: {e}")
            continue

        for endpoint in data.get("endpoints", []):
            raw_name = endpoint.get("name", "")
            url = endpoint.get("url") or ""
            # Skip non-peer endpoints
            if raw_name in ("ollama", "litellm-cloud"):
                continue
            # Canonical name (or raw if no alias)
            display_name = CANONICAL_NAMES.get(raw_name, raw_name)
            # Skip if this resolves to a real shepherd-node peer (would duplicate)
            if display_name in real_peer_names:
                continue
            # Dedup across multiple Ollas by canonical name (so cluster-llm's "1" and
            # Phoenix's "10_10_0_215" both resolve to "nuk1" and only one card appears)
            if display_name in seen_endpoint_names:
                continue
            seen_endpoint_names.add(display_name)

            status = endpoint.get("status", "unknown")
            models_count = endpoint.get("models", {}).get("count", 0)
            # If URL is missing, synthesize from raw name (e.g., "10_10_0_215" → "http://10.10.0.215:11434")
            if not url and raw_name.startswith("10_") and raw_name.count("_") == 3:
                url = "http://" + raw_name.replace("_", ".") + ":11434"
            resident_models = await fetch_remote_ollama_models(url) if (status == "healthy" and url) else []

            lite_peers.append({
                "name": display_name,
                "address": url,
                "olla_endpoint_name": raw_name,
                "role": "herd peer (lite via Olla)",
                "reachable": status == "healthy",
                "data_quality": "lite",
                "hardware": {
                    "accelerator_type": "unknown",
                    "accelerator_name": "via Olla federation (deploy shepherd-node for full data)",
                    "implementation_status": "stub",
                },
                "system": None,
                "ollama": {
                    "responding": status == "healthy",
                    "resident_models": resident_models,
                    "model_count_via_olla": models_count,
                },
                "olla": {
                    "responding": True,
                    "status_via_local_olla": status,
                    "issues": endpoint.get("issues", ""),
                    "discovered_via": _normalize_discovery_source(olla_url),
                    "discovered_via_raw": olla_url,
                },
            })
    return lite_peers


async def poll_all_peers():
    """Single sweep over real peers + Olla-derived lite peers; updates _snapshot.

    After each sweep, Layer 3.5 auto-recovery checks each real peer's verification
    state and POSTs /herd/recover when divergence is detected (with circuit breaker).
    """
    global _snapshot
    real = await asyncio.gather(*[poll_one_peer(n, u) for n, u in PEERS])
    lite = await derive_olla_peers()
    print(f"[shepherd-control] poll → {len(real)} real + {len(lite)} lite peers: {[n.get('name') for n in lite]}")
    _snapshot = {
        "nodes": list(real) + list(lite),
        "timestamp": time.time(),
        "source": "shepherd-control",
        "peer_counts": {"full": len(real), "lite": len(lite)},
        "recovery_state": dict(_recovery_state),  # snapshot for dashboard transparency
    }

    # Layer 3.5: auto-recovery sweep
    await asyncio.gather(*[maybe_recover_peer(p) for p in real])


async def poll_loop():
    while True:
        try:
            await poll_all_peers()
        except Exception as e:
            print(f"[shepherd-control] poll error: {e}")
        await asyncio.sleep(_poll_interval_s)


app = FastAPI(title="Shepherd Control", version="0.1.1")


@app.on_event("startup")
async def on_startup():
    await poll_all_peers()
    asyncio.create_task(poll_loop())


@app.get("/herd/aggregate")
async def aggregate():
    return _snapshot


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "real_peers": len(PEERS),
        "lite_peers_via_olla": _snapshot.get("peer_counts", {}).get("lite", 0),
        "snapshot_age_s": time.time() - (_snapshot.get("timestamp") or time.time()),
    }


_UI_DIR = Path(__file__).parent.parent / "ui"
if _UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR)), name="ui")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(_UI_DIR / "index.html")
