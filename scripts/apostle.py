#!/usr/bin/env python3
"""
Apostle — Self-organizing AI cluster node.

Every node runs this to introspect hardware, discover peers, decide which
models to host, acquire missing models over LAN (HTTP blob fetch from
peer Ollamas), and keep local routing configs in sync with the herd.

Usage:
  apostle status        → cluster health + model inventory
  apostle sync          → reconcile missing models
  apostle peers         → list known peers
  apostle catalog       → show model catalog filtered by this node
  apostle serve         → start HTTP API + dashboard (default port 40116)
  apostle serve --port N → start on custom port
"""

import http.server
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
ENV_FILE = Path(os.environ.get("APOSTLE_ENV_FILE", str(PROJECT_DIR / ".env")))
MODELS_YAML = SCRIPT_DIR / "models.yaml"
OLLA_YAML = PROJECT_DIR / "proxy" / "olla.yaml"
OLLAMA_PORT = 11434
OLLA_PORT = 40114
APOSTLE_PORT = int(os.environ.get("APOSTLE_PORT", "40116"))
APOSTLE_EVENTS_INTERVAL = float(os.environ.get("APOSTLE_EVENTS_INTERVAL", "5"))
APOSTLE_SYNC_INTERVAL = int(os.environ.get("APOSTLE_SYNC_INTERVAL", "1800"))
APOSTLE_MAX_DISK_PCT  = int(os.environ.get("APOSTLE_MAX_DISK_PCT", "85"))
MAX_AUTO_PULL_GB      = float(os.environ.get("MAX_AUTO_PULL_GB", "10"))
APOSTLE_MAINTENANCE   = os.environ.get("APOSTLE_MAINTENANCE", "").lower() in ("1", "true", "yes")
_OFFHOURS_START       = int(os.environ.get("APOSTLE_OFFHOURS_START", "22"))
_OFFHOURS_END         = int(os.environ.get("APOSTLE_OFFHOURS_END", "6"))
_ollama_host = os.environ.get("OLLAMA_HOST", "")
OLLAMA_URL = (
    f"http://{_ollama_host}:{OLLAMA_PORT}" if _ollama_host
    else os.environ.get("OLLAMA_URL", f"http://localhost:{OLLAMA_PORT}")
)

# ── Colour ──────────────────────────────────────────────────────────────────
def c(text, code):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text

ok   = lambda t: c(t, "0;32")
warn = lambda t: c(t, "1;33")
fail = lambda t: c(t, "0;31")
head = lambda t: c(t, "1")
dim  = lambda t: c(t, "2")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Hardware Oracle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def introspect():
    mem_total, mem_avail = 0, 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) // 1024 // 1024
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1]) // 1024 // 1024
    except OSError:
        pass

    gpu_info = []
    try:
        out = subprocess.check_output(
            "lspci 2>/dev/null | grep -iE '(vga|3d|display).*(intel|nvidia|amd)'",
            shell=True, text=True, timeout=5
        )
        for line in out.strip().split("\n"):
            line = line.strip()
            if line and "Host bridge" not in line and "USB" not in line:
                gpu_info.append(line)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    disk = {}
    try:
        out = subprocess.check_output(
            "df -BG / 2>/dev/null | tail -1", shell=True, text=True, timeout=5
        )
        parts = out.split()
        if len(parts) >= 4:
            disk = {"total_gb": int(parts[1].rstrip("G")),
                    "used_gb": int(parts[2].rstrip("G")),
                    "avail_gb": int(parts[3].rstrip("G"))}
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass

    is_laptop = os.path.isdir("/sys/class/power_supply") and any(
        p.startswith("BA") for p in os.listdir("/sys/class/power_supply")
    )

    ollama_ver = "unknown"
    try:
        out = subprocess.check_output(
            "ollama --version 2>/dev/null || "
            "docker exec ollama ollama --version 2>/dev/null",
            shell=True, text=True, timeout=5
        )
        ollama_ver = out.strip().split()[-1] if out.strip() else "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return {
        "ram_total_gb": mem_total,
        "ram_avail_gb": mem_avail,
        "gpu": gpu_info,
        "disk": disk,
        "is_laptop": is_laptop,
        "ollama_version": ollama_ver,
    }


def profile(hw):
    ram = hw.get("ram_total_gb", 0)
    if ram >= 32:
        return "server"
    elif ram >= 16:
        return "desktop"
    elif ram >= 8:
        return "laptop"
    else:
        return "ultra-light"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Model Catalog
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_catalog():
    if not MODELS_YAML.exists():
        return []
    with open(MODELS_YAML) as f:
        if yaml:
            data = yaml.safe_load(f)
        else:
            raise RuntimeError("PyYAML not available")
    return data.get("models", [])


def select_models(catalog, hw, ram_budget=0.7):
    profile_name = profile(hw)
    ram_limit = hw.get("ram_total_gb", 0) * ram_budget

    critical = [m for m in catalog if m.get("priority") == "critical"]
    rest = sorted(
        [m for m in catalog if m.get("priority") != "critical"],
        key=lambda m: {"high": 0, "medium": 1, "low": 2}.get(m.get("priority"), 3)
    )

    selected = list(critical)
    budget = ram_limit - sum(m.get("min_ram_gb", 0) for m in critical)

    for model in rest:
        needed = model.get("min_ram_gb", 0)
        if profile_name == "laptop" and needed > 8:
            continue
        if profile_name == "ultra-light" and needed > 3:
            continue
        if needed <= budget:
            selected.append(model)
            budget -= needed

    return selected


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Peer Discovery
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_env_peers():
    peers = []
    if not ENV_FILE.exists():
        return peers
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("OLLAMA_REMOTE_") and "=" in line:
                url = line.split("=", 1)[1].strip()
                url = re.sub(r":[0-9]+$", "", url)  # strip weight suffix
                host = url.replace("http://", "").replace("https://", "").split(":")[0]
                peers.append({"host": host, "url": url})
    return peers


def ollama_api(host_or_url, path, timeout=5):
    if host_or_url.startswith("http"):
        url = f"{host_or_url}{path}"
    else:
        url = f"http://{host_or_url}:{OLLAMA_PORT}{path}"
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def query_peer_models(peer):
    data = ollama_api(peer["host"], "/api/tags")
    if data and "models" in data:
        return [m["name"] for m in data["models"]]
    return []


def discover_peers():
    known = load_env_peers()
    healthy = []
    for p in known:
        models = query_peer_models(p)
        if models:
            healthy.append({**p, "models": models, "status": "healthy"})
        else:
            healthy.append({**p, "models": [], "status": "unreachable"})
    return healthy


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Local Model Inventory
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def local_models():
    data = ollama_api(OLLAMA_URL, "/api/tags")
    if data and "models" in data:
        return {m["name"]: m for m in data["models"]}
    return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. Model Apostle — Transfer Engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ollama_data_dir():
    for p in [
        Path.home() / ".ollama" / "models",
        Path("/usr/share/ollama/.ollama/models"),
    ]:
        if p.exists():
            return p
    return Path.home() / ".ollama" / "models"


def fetch_manifest_from_peer(model_name, peer_host):
    """Fetch a model's manifest from a peer Apostle HTTP endpoint.
    Falls back to direct Ollama blob API if peer doesn't run Apostle yet.
    Returns parsed manifest dict, or None."""
    # Try Apostle HTTP API first (no SSH required)
    url = f"http://{peer_host}:{APOSTLE_PORT}/apostle/v1/manifest/{model_name}"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        pass
    return None


def fetch_blob_from_peer(digest, peer_host, dest_path):
    """Download a single blob from a peer's Ollama via HTTP.
    digest should be in sha256:xxxx format."""
    safe = digest.replace(":", "-")
    url = f"http://{peer_host}:{OLLAMA_PORT}/api/blobs/{safe}"
    try:
        resp = urllib.request.urlopen(url, timeout=300)
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
        return True
    except (urllib.error.URLError, OSError) as e:
        return False


def acquire_from_peer(model_name, peer_host):
    local_dir = ollama_data_dir()
    blobs_dir = local_dir / "blobs"
    manifests_dir = local_dir / "manifests"
    blobs_dir.mkdir(parents=True, exist_ok=True)

    name, tag = (model_name.split(":", 1) + [""])[:2]
    if not tag:
        tag = "latest"

    print(f"  {dim('→')} Reading manifest from {peer_host}...")

    manifest = fetch_manifest_from_peer(model_name, peer_host)
    if not manifest:
        print(f"  {warn('~')} Cannot read manifest from {peer_host} (peer may not run Apostle yet)")
        return None

    man_dir = manifests_dir / "registry.ollama.ai" / "library" / name
    man_dir.mkdir(parents=True, exist_ok=True)

    # Collect all digests: config + layers
    all_digests = [manifest.get("config", {}).get("digest", "")]
    all_digests += [l["digest"] for l in manifest.get("layers", [])]
    all_digests = [d for d in all_digests if d]

    total = len(all_digests)
    existing = 0
    fetched = 0
    failed = []

    for i, digest in enumerate(all_digests, 1):
        safe = digest.replace(":", "-")
        dest = blobs_dir / safe
        if dest.exists():
            existing += 1
            continue

        print(f"  {dim('→')} Blob {i}/{total}: {safe[:20]}...", end=" ")
        sys.stdout.flush()
        if fetch_blob_from_peer(digest, peer_host, dest):
            fetched += 1
            print(ok("✓"))
        else:
            failed.append(digest)
            print(fail("✗"))

    if failed:
        # Clean up partial blobs
        for d in failed:
            safe = d.replace(":", "-")
            (blobs_dir / safe).unlink(missing_ok=True)
        print(f"  {fail('✗')} {len(failed)} blob(s) failed to download")
        return False

    # Write manifest
    man_path = man_dir / tag
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"  {ok('✓')} Transferred {model_name} from {peer_host} "
          f"({fetched} new, {existing} cached)")
    return True


def acquire_via_pull(model_name):
    print(f"  {dim('→')} Pulling {model_name} from registry...")
    try:
        env = os.environ.copy()
        subprocess.run(
            ["ollama", "pull", model_name],
            check=True, timeout=7200, env=env
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    try:
        subprocess.run(
            ["docker", "exec", "ollama", "ollama", "pull", model_name],
            check=True, timeout=7200
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"  {fail('✗')} Failed to pull {model_name}")
        return False


def normalize_name(name):
    return name if ":" in name else f"{name}:latest"

def reconcile(desired, current, peers):
    desired_names = {normalize_name(m["name"]) for m in desired}
    current_names = set(current.keys())
    missing = desired_names - current_names

    actions = []
    for name in sorted(missing):
        source = None
        for p in peers:
            if name in p.get("models", []):
                source = p["host"]
                break
        actions.append({"model": name, "peer_source": source})

    extra = current_names - desired_names
    return actions, extra


def sync(desired, current, peers):
    actions, extra = reconcile(desired, current, peers)

    if not actions and not extra:
        print(f" {ok('✓')} All desired models present")
        return True

    if extra:
        print(f" {dim('→')} {len(extra)} model(s) present but not in desired list")
        for name in sorted(extra):
            print(f"   {dim(name)}")

    for action in actions:
        model = normalize_name(action["model"])
        peer = action["peer_source"]

        if peer:
            print(f" {dim('→')} {model} (from peer {peer})")
            result = acquire_from_peer(model, peer)
            if result is None:
                result = acquire_via_pull(model)
        else:
            print(f" {dim('→')} {model} (from registry)")
            result = acquire_via_pull(model)

        if result:
            print(f"   {ok('✓')} {model}")
        else:
            print(f"   {fail('✗')} {model}")

    return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. Config Writer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def update_olla_config(peers):
    gen_script = PROJECT_DIR / "scripts" / "generate-olla-config.sh"
    if gen_script.exists():
        try:
            subprocess.run(["bash", str(gen_script)], check=True, timeout=30)
            print(f" {ok('✓')} olla.yaml regenerated")
        except subprocess.CalledProcessError:
            print(f" {fail('✗')} olla.yaml regeneration failed")
    else:
        print(f" {dim('→')} Config update: olla.yaml regeneration not yet automated")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. Self-Healing Engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_sync_lock = threading.Lock()
_sync_state: dict = {
    "running": False,
    "in_progress": [],
    "last_run": None,
    "next_run": None,
    "acquired": [],
    "failed": [],
    "skipped": [],
}


def _is_offhours():
    h = time.localtime().tm_hour
    s, e = _OFFHOURS_START, _OFFHOURS_END
    return (h >= s or h < e) if s > e else (s <= h < e)


def _disk_pct():
    d = introspect().get("disk", {})
    total = d.get("total_gb", 0)
    used = d.get("used_gb", 0)
    return int(used * 100 / total) if total > 0 else 0


def _peer_in_progress(model_name):
    for p in load_env_peers():
        try:
            url = f"http://{p['host']}:{APOSTLE_PORT}/apostle/v1/sync"
            resp = urllib.request.urlopen(url, timeout=3)
            data = json.loads(resp.read().decode())
            if model_name in data.get("in_progress", []):
                return p["host"]
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            pass
    return None


def ollama_pull_via_api(model_name):
    url = f"{OLLAMA_URL}/api/pull"
    payload = json.dumps({"name": model_name, "stream": False}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=7200)
        result = json.loads(resp.read().decode())
        return result.get("status") == "success"
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return False


def _run_sync_cycle(manual=False):
    with _sync_lock:
        if _sync_state["running"]:
            return
        _sync_state["running"] = True
        _sync_state["in_progress"] = []
        _sync_state["skipped"] = []

    try:
        if APOSTLE_MAINTENANCE and not manual:
            with _sync_lock:
                _sync_state["skipped"].append("maintenance mode active")
            return

        pct = _disk_pct()
        if pct >= APOSTLE_MAX_DISK_PCT:
            with _sync_lock:
                _sync_state["skipped"].append(f"disk {pct}% >= limit {APOSTLE_MAX_DISK_PCT}%")
            return

        cat = load_catalog() if MODELS_YAML.exists() else []
        if not cat:
            return

        hw = introspect()
        desired = select_models(cat, hw)
        current = local_models()
        peers = discover_peers()
        actions, _ = reconcile(desired, current, peers)

        for action in actions:
            model = normalize_name(action["model"])
            peer_src = action.get("peer_source")

            pulling_peer = _peer_in_progress(model)
            if pulling_peer:
                with _sync_lock:
                    _sync_state["skipped"].append(f"{model} (waiting on {pulling_peer})")
                continue

            if not peer_src and not manual:
                info = next((m for m in cat if normalize_name(m["name"]) == model), {})
                if info.get("disk_gb", 0) > MAX_AUTO_PULL_GB and not _is_offhours():
                    with _sync_lock:
                        _sync_state["skipped"].append(
                            f"{model} (>{MAX_AUTO_PULL_GB}GB, deferred to off-hours)"
                        )
                    continue

            with _sync_lock:
                _sync_state["in_progress"].append(model)

            success = False
            try:
                if peer_src:
                    result = acquire_from_peer(model, peer_src)
                    success = result is True
                    if not success:
                        success = ollama_pull_via_api(model) or acquire_via_pull(model)
                else:
                    success = ollama_pull_via_api(model) or acquire_via_pull(model)
            finally:
                with _sync_lock:
                    _sync_state["in_progress"] = [
                        m for m in _sync_state["in_progress"] if m != model
                    ]
                    if success:
                        _sync_state["acquired"].append(model)
                    else:
                        _sync_state["failed"].append(model)
    finally:
        with _sync_lock:
            _sync_state["running"] = False
            _sync_state["last_run"] = time.time()
            _sync_state["next_run"] = time.time() + APOSTLE_SYNC_INTERVAL


def _start_sync_daemon():
    def _loop():
        time.sleep(10)
        _run_sync_cycle()
        while True:
            time.sleep(APOSTLE_SYNC_INTERVAL)
            _run_sync_cycle()

    threading.Thread(target=_loop, name="apostle-sync", daemon=True).start()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. HTTP API Server  (apostle serve)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DASHBOARD_HTML = (
    b"<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
    b"<meta charset=\"utf-8\">\n"
    b"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
    b"<title>Apostle &#8212; Cluster</title>\n"
    b"<script src=\"https://d3js.org/d3.v7.min.js\"></script>\n"
    b"<style>\n"
    b"*{box-sizing:border-box;margin:0;padding:0}\n"
    b"body{background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif;"
    b"height:100vh;display:flex;flex-direction:column;overflow:hidden}\n"
    b"#hdr{display:flex;align-items:center;gap:2rem;padding:.75rem 1.5rem;"
    b"background:#161b22;border-bottom:1px solid #30363d;flex-shrink:0}\n"
    b"#hdr h1{font-size:1rem;font-weight:600;color:#e6edf3;white-space:nowrap}\n"
    b".stat{text-align:center}\n"
    b".stat .v{font-size:1.5rem;font-weight:700;color:#58a6ff;line-height:1}\n"
    b".stat .l{font-size:.65rem;text-transform:uppercase;letter-spacing:.05em;"
    b"color:#8b949e;margin-top:.15rem}\n"
    b"#dot{width:8px;height:8px;border-radius:50%;background:#3fb950;margin-left:auto;"
    b"flex-shrink:0;transition:background .3s}\n"
    b"#dot.stale{background:#f85149}\n"
    b"#wrap{flex:1;overflow:hidden;position:relative}\n"
    b"svg{width:100%;height:100%}\n"
    b".node-host circle{stroke-width:2;cursor:pointer}\n"
    b".local circle{fill:#152a1e;stroke:#3fb950}\n"
    b".healthy circle{fill:#1c2433;stroke:#58a6ff}\n"
    b".unreachable circle{fill:#2d1c1c;stroke:#f85149}\n"
    b".node-model circle{fill:#21262d;stroke:#6e7681;stroke-width:1}\n"
    b".node-host text,.node-model text{text-anchor:middle;dominant-baseline:central;"
    b"pointer-events:none}\n"
    b".node-host text{font-size:11px;fill:#e6edf3}\n"
    b".node-model text{font-size:9px;fill:#8b949e}\n"
    b".link{stroke:#30363d;stroke-opacity:.5}\n"
    b"#tip{position:absolute;top:0;left:0;background:#161b22;border:1px solid #30363d;"
    b"border-radius:6px;padding:.6rem .9rem;font-size:.8rem;pointer-events:none;"
    b"opacity:0;transition:opacity .15s;max-width:260px;line-height:1.5}\n"
    b"#tip h3{color:#e6edf3;margin-bottom:.3rem;font-size:.85rem}\n"
    b"#tip p{color:#8b949e}\n"
    b".ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}\n"
    b"</style>\n</head>\n<body>\n"
    b"<div id=\"hdr\">\n"
    b"  <h1>&#127776; Apostle Cluster</h1>\n"
    b"  <div class=\"stat\"><div class=\"v\" id=\"sn\">-</div>"
    b"<div class=\"l\">Nodes</div></div>\n"
    b"  <div class=\"stat\"><div class=\"v\" id=\"sm\">-</div>"
    b"<div class=\"l\">Models</div></div>\n"
    b"  <div class=\"stat\"><div class=\"v\" id=\"sh\">-</div>"
    b"<div class=\"l\">Healthy</div></div>\n"
    b"  <div class=\"stat\"><div class=\"v\" id=\"sy\">&#8212;</div>"
    b"<div class=\"l\">Sync</div></div>\n"
    b"  <div id=\"dot\" title=\"Live stream\"></div>\n"
    b"</div>\n"
    b"<div id=\"wrap\"><svg id=\"g\"></svg></div>\n"
    b"<div id=\"tip\"></div>\n"
    b"<script>\n"
    b"const svg=d3.select('#g');\n"
    b"const tip=document.getElementById('tip');\n"
    b"let sim,W,H;\n"
    b"function resize(){\n"
    b"  const el=document.getElementById('wrap');\n"
    b"  W=el.clientWidth;H=el.clientHeight;\n"
    b"  svg.attr('viewBox',`0 0 ${W} ${H}`);\n"
    b"  if(sim)sim.force('center',d3.forceCenter(W/2,H/2)).alpha(.3).restart();\n"
    b"}\n"
    b"window.addEventListener('resize',resize);resize();\n"
    b"function toGraph(d){\n"
    b"  const nodes=[],links=[];\n"
    b"  function host(id,label,status,hw,models,prof){\n"
    b"    nodes.push({id,type:'host',label,status,hw:hw||{},"
    b"models:models||{local:[],missing:[]},prof:prof||'?'});\n"
    b"  }\n"
    b"  function model(id,label,status,hostId){\n"
    b"    nodes.push({id,type:'model',label,status,hostId});\n"
    b"    links.push({source:hostId,target:id});\n"
    b"  }\n"
    b"  const L=d.local;\n"
    b"  host('__local',L.hostname,'local',L.hardware,L.models,L.profile);\n"
    b"  (L.models.local||[]).forEach(m=>model('lm:'+m,m.split(':')[0],'local','__local'));\n"
    b"  (d.peers||[]).forEach(p=>{\n"
    b"    const pid='p:'+p.hostname;\n"
    b"    host(pid,p.hostname,p.status,p.apostle&&p.apostle.hardware,\n"
    b"      p.apostle&&p.apostle.models||{local:p.models||[]},\n"
    b"      p.apostle&&p.apostle.profile);\n"
    b"    (p.models||[]).forEach(m=>model('pm:'+pid+':'+m,m.split(':')[0],p.status,pid));\n"
    b"  });\n"
    b"  return {nodes,links};\n"
    b"}\n"
    b"function render(graph){\n"
    b"  svg.selectAll('*').remove();\n"
    b"  const g=svg.append('g');\n"
    b"  svg.call(d3.zoom().scaleExtent([.2,4]).on('zoom',e=>g.attr('transform',e.transform)));\n"
    b"  sim=d3.forceSimulation(graph.nodes)\n"
    b"    .force('link',d3.forceLink(graph.links).id(d=>d.id)\n"
    b"      .distance(d=>d.target.type==='model'?80:180).strength(.7))\n"
    b"    .force('charge',d3.forceManyBody().strength(d=>d.type==='host'?-350:-90))\n"
    b"    .force('center',d3.forceCenter(W/2,H/2))\n"
    b"    .force('collide',d3.forceCollide(d=>d.type==='host'?50:20));\n"
    b"  const linkSel=g.append('g').selectAll('line').data(graph.links).join('line')"
    b".attr('class','link');\n"
    b"  const nodeSel=g.append('g').selectAll('g').data(graph.nodes).join('g')\n"
    b"    .attr('class',d=>`node-${d.type} ${d.status}`)\n"
    b"    .call(d3.drag()\n"
    b"      .on('start',(e,d)=>{if(!e.active)sim.alphaTarget(.3).restart();"
    b"d.fx=d.x;d.fy=d.y;})\n"
    b"      .on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y;})\n"
    b"      .on('end',(e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}))\n"
    b"    .on('mouseover',showTip)\n"
    b"    .on('mouseout',()=>{tip.style.opacity=0;});\n"
    b"  nodeSel.append('circle').attr('r',d=>d.type==='host'?34:13);\n"
    b"  nodeSel.filter(d=>d.type==='host').append('text').text(d=>{\n"
    b"    const s=d.label;return s.length>12?s.slice(0,11)+'\\u2026':s;\n"
    b"  });\n"
    b"  nodeSel.filter(d=>d.type==='model').append('text').attr('dy','.35em').text(d=>{\n"
    b"    const s=d.label;return s.length>10?s.slice(0,9)+'\\u2026':s;\n"
    b"  });\n"
    b"  sim.on('tick',()=>{\n"
    b"    linkSel.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y)\n"
    b"           .attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);\n"
    b"    nodeSel.attr('transform',d=>`translate(${d.x},${d.y})`);\n"
    b"  });\n"
    b"}\n"
    b"function showTip(e,d){\n"
    b"  const wrap=document.getElementById('wrap').getBoundingClientRect();\n"
    b"  if(d.type==='model'){\n"
    b"    tip.innerHTML=`<h3>${d.label}</h3><p>Host: "
    b"${d.hostId==='__local'?'(this node)':d.hostId.slice(2)}</p>`;\n"
    b"  }else{\n"
    b"    const hw=d.hw||{};const miss=(d.models.missing||[]).length;\n"
    b"    const cnt=(d.models.local||[]).length;\n"
    b"    tip.innerHTML=`<h3>${d.label}</h3>\n"
    b"      <p>Profile: ${d.prof}</p>\n"
    b"      <p>RAM: ${hw.ram_total_gb||'?'}GB total / ${hw.ram_avail_gb||'?'}GB free</p>\n"
    b"      <p>Models: <span class=\\\"ok\\\">${cnt} loaded</span>"
    b"${miss?' <span class=\\\"warn\\\">'+miss+' missing</span>':''}</p>\n"
    b"      <p>Status: <span class=\\\"${d.status==='local'||d.status==='healthy'?"
    b"'ok':'bad'}\\\">${d.status}</span></p>`;\n"
    b"    if(hw.gpu&&hw.gpu.length)tip.innerHTML+=`<p>GPU: ${hw.gpu[0]}</p>`;\n"
    b"  }\n"
    b"  let tx=e.clientX-wrap.left+14,ty=e.clientY-wrap.top+14;\n"
    b"  if(tx+270>W)tx-=284;if(ty+120>H)ty-=130;\n"
    b"  tip.style.left=tx+'px';tip.style.top=ty+'px';tip.style.opacity=1;\n"
    b"}\n"
    b"function update(data){\n"
    b"  document.getElementById('sn').textContent="
    b"data.cluster&&data.cluster.node_count!=null?data.cluster.node_count:'-';\n"
    b"  document.getElementById('sm').textContent="
    b"data.cluster&&data.cluster.total_models!=null?data.cluster.total_models:'-';\n"
    b"  document.getElementById('sh').textContent="
    b"data.cluster&&data.cluster.healthy_peers!=null?data.cluster.healthy_peers:'-';\n"
    b"  document.getElementById('dot').classList.remove('stale');\n"
    b"  render(toGraph(data));\n"
    b"}\n"
    b"fetch('/apostle/v1/cluster').then(r=>r.json()).then(update).catch(()=>{});\n"
    b"const es=new EventSource('/apostle/v1/events');\n"
    b"let staleT;\n"
    b"es.onmessage=e=>{\n"
    b"  clearTimeout(staleT);\n"
    b"  staleT=setTimeout(()=>document.getElementById('dot').classList.add('stale'),20000);\n"
    b"  try{update(JSON.parse(e.data));}catch(ex){}\n"
    b"};\n"
    b"es.onerror=()=>document.getElementById('dot').classList.add('stale');\n"
    b"function updateSync(){\n"
    b"  fetch('/apostle/v1/sync').then(r=>r.json()).then(s=>{\n"
    b"    const el=document.getElementById('sy');\n"
    b"    if(s.running)el.textContent='...';\n"
    b"    else if(s.last_run){\n"
    b"      const d=new Date(s.last_run*1000);\n"
    b"      el.textContent=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});\n"
    b"    }else el.textContent='\\u2014';\n"
    b"  }).catch(()=>{});\n"
    b"}\n"
    b"updateSync();setInterval(updateSync,30000);\n"
    b"</script>\n</body>\n</html>\n"
)

def _cluster_snapshot():
    hw = introspect()
    cat = load_catalog() if MODELS_YAML.exists() else []
    desired = select_models(cat, hw) if cat else []
    current = local_models()
    peers = discover_peers()
    desired_names = {normalize_name(m["name"]) for m in desired}
    all_models: set = set(current.keys())
    peer_nodes = []
    for p in peers:
        node = {
            "hostname": p.get("host", ""),
            "url": p.get("url", ""),
            "models": p.get("models", []),
            "status": p.get("status", "unknown"),
            "apostle": None,
        }
        try:
            aurl = f"http://{p['host']}:{APOSTLE_PORT}/apostle/v1/status"
            resp = urllib.request.urlopen(aurl, timeout=3)
            node["apostle"] = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            pass
        peer_nodes.append(node)
        all_models.update(p.get("models", []))
    return {
        "local": {
            "hostname": socket.gethostname(),
            "profile": profile(hw),
            "hardware": hw,
            "models": {
                "desired": [m["name"] for m in desired],
                "local": list(current.keys()),
                "missing": sorted(desired_names - set(current.keys())),
            },
            "status": "local",
        },
        "peers": peer_nodes,
        "cluster": {
            "node_count": 1 + len(peers),
            "total_models": len(all_models),
            "healthy_peers": sum(1 for p in peers if p.get("status") == "healthy"),
        },
        "sync": dict(_sync_state),
        "timestamp": time.time(),
    }


class _ApostleHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default per-request logging

    def _json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url_path = self.path.split("?")[0].rstrip("/")
        url_path = url_path.replace("%3A", ":").replace("%3a", ":")

        if url_path in ("", "/", "/ui"):
            self._html(_DASHBOARD_HTML)
        elif url_path == "/apostle/v1/status":
            self._handle_status()
        elif url_path == "/apostle/v1/cluster":
            self._handle_cluster()
        elif url_path == "/apostle/v1/events":
            self._handle_sse()
        elif url_path.startswith("/apostle/v1/manifest/"):
            self._handle_manifest(url_path[len("/apostle/v1/manifest/"):])
        elif url_path == "/apostle/v1/sync":
            self._handle_sync_get()
        elif url_path in ("/health", "/apostle/health"):
            self._json({"status": "ok", "hostname": socket.gethostname()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        url_path = self.path.split("?")[0].rstrip("/")
        if url_path == "/apostle/v1/sync":
            self._handle_sync_post()
        else:
            self._json({"error": "not found"}, 404)

    def _handle_sync_get(self):
        with _sync_lock:
            state = dict(_sync_state)
        self._json(state)

    def _handle_sync_post(self):
        threading.Thread(
            target=_run_sync_cycle, kwargs={"manual": True}, daemon=True
        ).start()
        self._json({"status": "sync triggered"})

    def _handle_status(self):
        hw = introspect()
        cat = load_catalog() if MODELS_YAML.exists() else []
        desired = select_models(cat, hw) if cat else []
        current = local_models()
        node_profile = profile(hw)
        desired_names = {normalize_name(m["name"]) for m in desired}
        self._json({
            "hostname": socket.gethostname(),
            "profile": node_profile,
            "hardware": hw,
            "models": {
                "desired": [m["name"] for m in desired],
                "local": list(current.keys()),
                "missing": sorted(desired_names - set(current.keys())),
            },
            "timestamp": time.time(),
        })

    def _handle_cluster(self):
        self._json(_cluster_snapshot())

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                data = json.dumps(_cluster_snapshot())
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
                time.sleep(APOSTLE_EVENTS_INTERVAL)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass



    def _handle_manifest(self, model_name):
        if not model_name:
            self._json({"error": "model name required"}, 400)
            return
        name, tag = (model_name.split(":", 1) + [""])[:2]
        if not tag:
            tag = "latest"
        for base in [
            Path.home() / ".ollama" / "models",
            Path("/usr/share/ollama/.ollama/models"),
        ]:
            candidate = (
                base / "manifests" / "registry.ollama.ai" / "library" / name / tag
            )
            if candidate.exists():
                try:
                    with open(candidate) as f:
                        self._json(json.load(f))
                    return
                except (OSError, json.JSONDecodeError):
                    pass
        self._json({"error": f"manifest not found: {model_name}"}, 404)


class _ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def cmd_serve(port=None):
    if port is None:
        port = APOSTLE_PORT
    server = _ThreadedServer(("", port), _ApostleHandler)
    hostname = socket.gethostname()
    print(f"\n {head('Apostle')} HTTP API · {hostname} · port {port}")
    print(f"  {dim('Dashboard:')} http://localhost:{port}/ui")
    print(f"  {dim('Events:')}    http://localhost:{port}/apostle/v1/events  (SSE)")
    print(f"  {dim('Status:')}    http://localhost:{port}/apostle/v1/status")
    print(f"  {dim('Cluster:')}   http://localhost:{port}/apostle/v1/cluster")
    print(f"  {dim('Manifest:')}  http://localhost:{port}/apostle/v1/manifest/<model>")
    print(f"  {dim('Health:')}    http://localhost:{port}/health")
    print(f"  {dim('Sync:')}      http://localhost:{port}/apostle/v1/sync")
    print(f"\n  {dim('Press Ctrl+C to stop')}\n")
    _start_sync_daemon()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n {ok('✓')} Server stopped")
        server.server_close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cmd_status():
    hw = introspect()
    cat = load_catalog()
    desired = select_models(cat, hw)
    current = local_models()
    peers = discover_peers()

    p = profile(hw)
    print(f"\n {head('Apostle')} — {p}")
    print(f" {dim('Hardware:')} {hw.get('ram_total_gb', '?')}GB RAM, "
          f"{hw.get('disk', {}).get('avail_gb', '?')}GB disk free, "
          f"Ollama {hw.get('ollama_version', '?')}")
    if hw["gpu"]:
        for g in hw["gpu"]:
            print(f" {dim('GPU:')} {g}")
    print(f" {dim('Laptop:')} {'yes' if hw['is_laptop'] else 'no'}")

    print(f"\n {head('Models')}")
    print(f"  Desired: {len(desired)}  |  Local: {len(current)}  |  "
          f"Missing: {len(set(normalize_name(m['name']) for m in desired) - set(current.keys()))}")

    for m in desired:
        name = normalize_name(m["name"])
        mark = ok("✓") if name in current else fail("✗")
        pri = m.get("priority", "?")
        ram = m.get("min_ram_gb", "?")
        print(f"  {mark} {name:{30}} [{pri:8}] ~{ram}GB RAM")

    print(f"\n {head('Peers')}  ({len(peers)} known)")
    for p in peers:
        s = ok("✓") if p["status"] == "healthy" else fail("✗")
        print(f"  {s} {p['host']:{16}} {len(p.get('models',[]))} models  [{p['status']}]")

    if desired:
        actions, extra = reconcile(desired, current, peers)
        if actions:
            print(f"\n {warn('→')} {len(actions)} model(s) need syncing")
            for a in actions:
                src = a["peer_source"] or "registry"
                print(f"     {a['model']} ← {src}")
        else:
            print(f"\n {ok('✓')} Up to date")


def cmd_sync():
    hw = introspect()
    cat = load_catalog()
    desired = select_models(cat, hw)
    current = local_models()
    peers = discover_peers()

    print(f" {head('Apostle Sync')}")
    print(f" {dim(f'Desired: {len(desired)}  Local: {len(current)}  Peers: {len(peers)}')}\n")
    sync(desired, current, peers)
    print(f"\n {ok('✓')} Sync complete")


def cmd_peers():
    peers = discover_peers()
    print(f"\n {head('Peers')}")
    for p in peers:
        s = ok("✓") if p["status"] == "healthy" else fail("✗")
        print(f"  {s} {p['host']}")
        for m in p.get("models", []):
            print(f"       {dim(m)}")
        if not p["models"] and p["status"] == "unreachable":
            print(f"       {dim('(unreachable)')}")


def cmd_catalog():
    hw = introspect()
    cat = load_catalog()
    selected = select_models(cat, hw)
    p = profile(hw)

    print(f"\n {head('Model Catalog')}  — profile: {p}, "
          f"{hw.get('ram_total_gb', '?')}GB RAM budget (70%)")
    print(f"  {len(selected)} of {len(cat)} models selected\n")

    for m in cat:
        name = m["name"]
        selected_flag = ok("✓") if m in selected else dim(" ")
        print(f"  {selected_flag} {name:{30}} "
              f"{m['priority']:{8}} ~{str(m.get('min_ram_gb','?'))+'GB':{5}} RAM  "
              f"{'tools' if m.get('tools') else '     '}  {m.get('role','')}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        cmd_status()
    elif cmd == "sync":
        cmd_sync()
    elif cmd == "peers":
        cmd_peers()
    elif cmd == "catalog":
        cmd_catalog()
    elif cmd == "serve":
        port = None
        if "--port" in sys.argv:
            idx = sys.argv.index("--port")
            if idx + 1 < len(sys.argv):
                port = int(sys.argv[idx + 1])
        cmd_serve(port)
    else:
        print(f"Usage: {sys.argv[0]} {{status|sync|peers|catalog|serve [--port N]}}")
        sys.exit(1)


if __name__ == "__main__":
    main()
