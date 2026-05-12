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
ENV_FILE = PROJECT_DIR / ".env"
MODELS_YAML = SCRIPT_DIR / "models.yaml"
OLLA_YAML = PROJECT_DIR / "proxy" / "olla.yaml"
OLLAMA_PORT = 11434
OLLA_PORT = 40114
APOSTLE_PORT = int(os.environ.get("APOSTLE_PORT", "40116"))

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


def ollama_api(host, path, timeout=5):
    url = f"http://{host}:{OLLAMA_PORT}{path}"
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
    data = ollama_api("localhost", "/api/tags")
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
# 7. HTTP API Server  (apostle serve)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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

    def do_GET(self):
        url_path = self.path.split("?")[0].rstrip("/")
        url_path = url_path.replace("%3A", ":").replace("%3a", ":")

        if url_path == "/apostle/v1/status":
            self._handle_status()
        elif url_path == "/apostle/v1/cluster":
            self._handle_cluster()
        elif url_path.startswith("/apostle/v1/manifest/"):
            self._handle_manifest(url_path[len("/apostle/v1/manifest/"):])
        elif url_path in ("/health", "/apostle/health"):
            self._json({"status": "ok", "hostname": socket.gethostname()})
        else:
            self._json({"error": "not found"}, 404)

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
        hw = introspect()
        cat = load_catalog() if MODELS_YAML.exists() else []
        desired = select_models(cat, hw) if cat else []
        current = local_models()
        peers = discover_peers()
        node_profile = profile(hw)

        local_node = {
            "hostname": socket.gethostname(),
            "profile": node_profile,
            "models": list(current.keys()),
            "status": "local",
        }

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

        all_models: set = set(current.keys())
        for p in peers:
            all_models.update(p.get("models", []))

        self._json({
            "coordinator": socket.gethostname(),
            "nodes": [local_node] + peer_nodes,
            "cluster": {
                "node_count": 1 + len(peers),
                "total_models": len(all_models),
                "healthy_peers": sum(
                    1 for p in peers if p.get("status") == "healthy"
                ),
            },
            "timestamp": time.time(),
        })

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
    print(f"  {dim('Status:')}   http://localhost:{port}/apostle/v1/status")
    print(f"  {dim('Cluster:')}  http://localhost:{port}/apostle/v1/cluster")
    print(f"  {dim('Manifest:')} http://localhost:{port}/apostle/v1/manifest/<model>")
    print(f"  {dim('Health:')}   http://localhost:{port}/health")
    print(f"\n  {dim('Press Ctrl+C to stop')}\n")
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
