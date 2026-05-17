# Multi-machine setup

ai-stack can span multiple computers. If you have more than one machine running Ollama, the Olla load balancer distributes requests across all of them. This gives you access to more models simultaneously and faster parallel processing.

---

## How it works

Olla maintains a list of Ollama endpoints. When a request comes in, it picks the best available endpoint based on load and health. You don't configure which machine handles which request — Olla handles that automatically.

```
OpenCode → Smart Router → Olla
                            ├── ollama (local, this machine)
                            ├── 192.168.1.50:11434 (workstation)
                            └── 192.168.1.75:11434 (server)
```

---

## Requirements for additional nodes

Each additional machine needs:
- Ollama installed and running
- Port `11434` reachable from your main machine
- The models you want available already pulled

Ollama does not need the full ai-stack — just the Ollama service itself.

### Installing Ollama on a second machine

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama

# Pull models on the remote machine
ollama pull llama3.1:8b
ollama pull qwen2.5-coder:14b
```

By default, Ollama only listens on `localhost`. To allow connections from other machines:

```bash
# Edit the Ollama service
sudo systemctl edit ollama.service
```

Add:
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Verify from your main machine:
```bash
curl http://192.168.1.50:11434/api/tags
```

---

## Adding machines manually

Edit `.env` on your main machine and add a line for each remote node:

```bash
OLLAMA_REMOTE_WORKSTATION=http://192.168.1.50:11434:75
OLLAMA_REMOTE_SERVER=http://192.168.1.75:11434:50
```

The format is `http://HOST:PORT:WEIGHT`. Weight controls how often Olla routes to this node relative to others (higher = more requests). The local `ollama` service defaults to weight 100.

Then regenerate the Olla config and restart:

```bash
bash scripts/generate-olla-config.sh
sudo systemctl restart ai-stack.service
```

Verify Olla sees all nodes:
```bash
curl http://localhost:40114/internal/status/endpoints
```

---

## Auto-discovery via mDNS

Instead of manually managing IP addresses, the `discover-herd.sh` script scans your local network for machines advertising Ollama via mDNS (`_ollama._tcp`). This works when machines are on the same local network segment.

```bash
# Discover nodes and preview what would be added
bash scripts/discover-herd.sh

# Discover and apply (updates olla.yaml and restarts Olla)
bash scripts/discover-herd.sh --apply
```

To advertise Ollama via mDNS on a remote machine, install `avahi-daemon` and register the service:

```bash
# On the remote machine
sudo apt install avahi-daemon

# Create an mDNS service registration
sudo tee /etc/avahi/services/ollama.service > /dev/null << 'EOF'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>Ollama on $HOSTNAME</name>
  <service>
    <type>_ollama._tcp</type>
    <port>11434</port>
  </service>
</service-group>
EOF

sudo systemctl restart avahi-daemon
```

Once registered, `discover-herd.sh` will find the machine automatically.

---

## Automatic periodic discovery

To keep node discovery up to date without manual intervention, set up a systemd timer:

```bash
# Copy the timer (included in ai-stack)
sudo cp systemd/ai-stack-discover.timer /etc/systemd/system/
sudo cp systemd/ai-stack-discover.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now ai-stack-discover.timer
```

This runs discovery every 5 minutes and updates Olla's config when new nodes appear.

---

## Multi-machine over the internet (NetBird VPN)

For machines on different networks — at home and at work, or distributed across locations — you need a secure tunnel between them.

The recommended approach is [NetBird](https://netbird.io), a WireGuard-based mesh VPN that automatically connects machines without manual port forwarding or firewall configuration.

### Setting up NetBird

1. Create a free account at [app.netbird.io](https://app.netbird.io)
2. Install the NetBird agent on each machine:
   ```bash
   curl -fsSL https://pkgs.netbird.io/install.sh | sh
   ```
3. Connect each machine:
   ```bash
   netbird up
   ```
   Follow the browser link to authenticate.

Once connected, each machine gets a stable IP in the `100.x.x.x` range (NetBird's virtual subnet). These IPs are stable across network changes and reboots.

### Adding NetBird nodes to Olla

Use the NetBird IP instead of the LAN IP:

```bash
OLLAMA_REMOTE_HOMELAB=http://100.64.0.5:11434:60
```

Regenerate config and restart as usual. The smart router and Olla work the same way regardless of whether the connection is local or over VPN.

### Network discovery over NetBird

The `discover-network.sh` script can scan the NetBird subnet in addition to the local LAN. Set the `NETBIRD_SUBNET` variable in `.env`:

```bash
NETBIRD_SUBNET=100.64.0.0/10
```

Then discovery automatically includes NetBird-connected machines.

---

## Monitoring distributed requests

To see how requests are being distributed across nodes:

```bash
# Real-time Olla routing
curl http://localhost:40114/internal/status/endpoints

# Which models are available across all nodes
curl http://localhost:40115/v1/router/capabilities
```

The smart router only routes to models that are available on at least one connected node. If a model is pulled on the remote machine but not the local one, the router will still use it — Olla handles the routing to wherever it lives.

---

## Herd observability with Shepherd

> *Replaces the earlier "Apostle" self-sync agent. Shepherd takes Apostle's observability role; model distribution today uses `ollama pull` per node, with Olla federation making "model available somewhere on the herd" usable transparently.*

[Shepherd](../shepherd/README.md) is the herd's observability layer — a per-node sidecar (`shepherd-node`) plus a central control-plane (`shepherd-control`). After your peers are connected via Olla (above), deploy Shepherd on each so they appear on the dashboard with real CPU/RAM/GPU stats.

### One-time setup on each peer

```bash
# On each herd peer (cluster-llm, lab nodes, Phoenix, etc.)
cd ~/Projects/ai-stack    # or wherever you cloned ai-stack
scripts/shepherd-auto-deploy.sh node
```

cluster-llm (or whichever node is the canonical control host) also runs:

```bash
scripts/shepherd-auto-deploy.sh both    # node + control-plane dashboard
```

### Auto-update pattern (recommended)

Set a daily cron on each peer so main-branch updates propagate without per-node operator SSH:

```bash
crontab -e
# Append (4:17am daily, off-peak):
# 17 4 * * * /home/<user>/ai-stack/scripts/shepherd-auto-deploy.sh node >> /tmp/shepherd-auto-deploy.log 2>&1
```

cluster-llm is the canonical edit/test node — you only push to `main` there. Other peers auto-pull and redeploy.

### Verifying

After deployment, open the control-plane dashboard:

```
http://<control-host>:40117/
```

Each healthy peer appears as a card with hardware vendor, accelerator status, resident models, and CPU/RAM gauges. Federation peers (via Olla) also appear, as "lite cards" showing model count without full host metrics — full metrics require `shepherd-node` deployed locally.

### Model sync between nodes

Initial population: use `sync-models.sh` (rsync push from cluster-llm), `pull_ollama_nodes.sh` (Docker-native pull), or just `ollama pull` per node. Olla then handles "model exists somewhere on the herd" routing transparently.
