# NetBird mesh wiring for the ai-stack herd

This is the operator procedure for wiring an ai-stack herd onto a NetBird
WireGuard mesh — a flat, persistent, encrypted overlay network that lets every
peer reach every other peer by a stable `100.x.x.x` address regardless of
underlying NAT / firewall / network changes.

> Companion to [`docs/multi-machine.md`](../multi-machine.md), which covers the
> short-form NetBird setup for one or two extra machines. This doc covers the
> full-herd case: 4-8+ nodes, mixed roles (control / compute / dev), some on a
> private LAN behind a single gateway peer.

Validated for the BMS-class herd composition: `cluster-llm` + `Phoenix` +
`nuk1` + `lab1-lab4` (or similar layout).

## Why NetBird (over alternatives)

| Option | Pros | Cons | Fit |
|---|---|---|---|
| **NetBird** (this doc) | Persistent IPs, NAT traversal, ACL groups, free tier covers a small herd, OSS agent | Account-tied; uses a hosted control plane unless self-hosted | ✅ herd of 4-10 mixed nodes |
| Cloudflare quick-tunnel | Zero-config | Ephemeral URLs rotate every restart; not suitable for persistent herd | ❌ demo-only |
| Plain WireGuard | Maximum control, no third party | Manual key management + manual config per peer; no NAT helper | △ small static herd only |
| Tailscale | Similar to NetBird | Account-tied like NetBird; trade-off comes down to operator preference | △ alternative path |

For the operator's stated direction (`project_garth_mesh_offer`, `b4472e8d` per-hardware-target OSS pattern, `5b3a726b` herd-offloading as TOP-priority): **NetBird is the recommended primary path**.

## Prerequisites

### ⚠️ STEP 0 — Operator-side credential delivery (blocks pod-execution)

The NetBird **setup-key** is the credential a new node uses to join the tailnet
without going through the interactive browser-OAuth flow. Without it, pod
sessions cannot self-join the mesh.

**Operator action required before pod can execute any wiring:**

1. Log into [app.netbird.io](https://app.netbird.io) with the herd's account
2. Create a reusable setup-key (Settings → Setup Keys → New)
   - **Type:** Reusable (so it works for multiple nodes)
   - **Usage limit:** ≥ herd-size (e.g., 10 for safety)
   - **Expiry:** 7 days (or longer if comfortable; reusable keys can be revoked)
   - **Auto-assigned groups:** `ai-stack-herd` (create if absent)
3. Email the setup-key to **`enclave.ai1@cascadesteam.org`** so the pod can read it from the inbox per the standing credential-handoff protocol
   - **DO NOT paste the key into a channel** — channel scrollback is leaky retention
   - The pod treats vault + email as the only acceptable transport

Once the key is in the mailbox, the pod can pick it up via `vault_get` or
`mail_search` and proceed with steps below. **Until then, all per-node steps
that say "join the tailnet" are blocked.**

### Per-node prerequisites

Each node needs:

- A user account with `sudo` access (the SSH user listed in
  `reference_herd_ssh_users` per peer — `gemini` on cluster-llm, `netyeti` on
  Phoenix and nuk1, `bmsadmin` on lab1-4)
- Outbound HTTPS reachable (NetBird control-plane contact)
- One UDP port open in the outbound direction for WireGuard data plane (NetBird
  handles NAT traversal automatically; no inbound forwarding required)
- Linux (the install steps below assume Debian/Ubuntu — adjust package commands
  for other distros)

## Architecture: herd layout on NetBird

```
                          NetBird control plane (app.netbird.io)
                                       │
                                       │  (control + key exchange)
                                       │
   ┌──────────────────────────────────┴─────────────────────────────────┐
   │                                                                    │
   │       NetBird tailnet — flat 100.x.x.x address space               │
   │                                                                    │
   │   cluster-llm           Phoenix             nuk1            lab1-4│
   │   100.123.141.125       100.123.227.178     (BMS LAN)      (BMS LAN)
   │   group: ai-stack       group: ai-stack    via routing peer or
   │   group: control        group: dev          direct-installed agent
   │                                                                    │
   └────────────────────────────────────────────────────────────────────┘

   BMS LAN (10.10.0.0/24, behind a NAT): nuk1 (.215) + lab1-4 (.211-.214)
```

Two integration choices for the BMS-LAN-only nodes (nuk1, lab1-4):

- **Option I — Install NetBird agent directly on each node.** Each gets its own
  `100.x.x.x` IP. Recommended for full herd visibility.
- **Option II — Use NetBird's "routing peer" feature.** One BMS-LAN node
  (typically Phoenix or a designated router) is a NetBird agent that announces
  `10.10.0.0/24` to the rest of the tailnet. Other peers reach nuk1/lab nodes
  by their LAN IPs through the routing peer. Saves per-node install cost; loses
  per-node visibility from outside the LAN.

**Vela-lean: Option I** (per-node install). Simplifies routing, gives every
node a stable mesh IP, makes shepherd-control's per-peer visibility work
uniformly across the herd.

## Wiring procedure — per node-class

> All steps below assume **Step 0 setup-key is in hand**. Substitute
> `$NETBIRD_SETUP_KEY` with the actual key value, or `export
> NETBIRD_SETUP_KEY=...` before running.

### A. cluster-llm (control + primary federation host)

cluster-llm is the canonical control host: runs Olla + Smart Router +
shepherd-control + has SSH ProxyJump config for BMS-LAN peers.

```bash
ssh gemini@cluster-llm

# Install NetBird agent
curl -fsSL https://pkgs.netbird.io/install.sh | sudo sh

# Join the tailnet using setup-key (no browser flow needed)
sudo netbird up --setup-key "$NETBIRD_SETUP_KEY"

# Verify
netbird status
# Expected: connected; assigned IP in 100.x.x.x range
ip addr show wt0
# Expected: wt0 interface with the same 100.x.x.x IP

# Note the assigned IP — used for ai-stack federation config below
```

### B. Phoenix (dev host + BMS-LAN bridge)

Phoenix is the operator's primary dev box + has been running ipex-llm ollama
locally. Often used as SSH jumphost into BMS LAN.

```bash
ssh netyeti@phoenix-ip   # current NetBird IP per reference_herd_ssh_users

# Same install + join as cluster-llm
curl -fsSL https://pkgs.netbird.io/install.sh | sudo sh
sudo netbird up --setup-key "$NETBIRD_SETUP_KEY"

netbird status
```

### C. nuk1 (BMS-LAN, native Vulkan Ollama)

nuk1 is on the BMS private LAN — currently reached via `cluster-llm` as
ProxyJump. Installing NetBird directly on nuk1 means it gets its own mesh IP
and is reachable from any tailnet peer without the proxy hop.

```bash
# From the operator's dev box, jump through cluster-llm
ssh -J gemini@cluster-llm netyeti@nuk1

# Install + join — same pattern
curl -fsSL https://pkgs.netbird.io/install.sh | sudo sh
sudo netbird up --setup-key "$NETBIRD_SETUP_KEY"

netbird status
# Note the assigned IP
```

### D. lab1, lab2, lab3, lab4 (BMS LAN)

Same as nuk1, but with the `bmsadmin` user (per `reference_herd_ssh_users`)
and ProxyJump alias from cluster-llm's SSH config (`bms-lab-1` ... `bms-lab-4`).

```bash
# For each lab node (loop over 1..4):
for LAB in 1 2 3 4; do
    ssh -J gemini@cluster-llm bmsadmin@bms-lab-${LAB} \
        "curl -fsSL https://pkgs.netbird.io/install.sh | sudo sh && \
         sudo netbird up --setup-key '$NETBIRD_SETUP_KEY' && \
         netbird status"
done
```

### E. Operator's dev box (optional but recommended)

If you want browser-clickable URLs into the herd from your laptop / desktop:

```bash
# On the dev box
curl -fsSL https://pkgs.netbird.io/install.sh | sh   # may not need sudo
netbird up --setup-key "$NETBIRD_SETUP_KEY"
```

Now `http://100.123.141.125:40117/` (or whatever cluster-llm's mesh IP is)
takes you straight to the Shepherd dashboard. Same for Olla `:40114`, Smart
Router `:40115/gestalt/ui`, LiteLLM `:4000/ui` — the hub-page (PR #41) becomes
the single bookmark.

## Group / policy configuration

In the NetBird admin UI ([app.netbird.io](https://app.netbird.io)):

1. **Create groups** (if not already present):
   - `ai-stack-herd` — every node in the herd
   - `ai-stack-control` — just cluster-llm (anchors Olla + Smart Router + Shepherd-control)
   - `ai-stack-compute` — Phoenix + nuk1 + lab1-4 (Ollama runners)
   - `ai-stack-dev` — operator's dev box (gets read-access to dashboards)

2. **Assign nodes to groups** under Peers → each peer → Edit.

3. **Define policies** under Access Control → Policies:
   - `ai-stack-herd → ai-stack-herd`: All ports / all protocols (intra-herd
     full mesh; required for federation + shepherd polling + ollama API
     forwarding).
   - `ai-stack-dev → ai-stack-control`: TCP ports 40114, 40115, 40117, 4000,
     42000 (dashboard + admin access from dev box).
   - `ai-stack-dev → ai-stack-compute`: TCP port 11434 (direct ollama API
     access from dev box, optional but useful for debugging).

Default-deny everything else.

## ai-stack integration: update federation config

Once mesh IPs are assigned, update `cluster-llm`'s ai-stack `.env` so Olla
prefers mesh addresses (stable) over LAN IPs (which may drift if BMS networking
changes):

```bash
ssh gemini@cluster-llm
cd ~/ai-stack    # or wherever the checkout lives

# Edit .env (or create overrides):
cat >> .env <<'EOF'

# ── Herd federation via NetBird mesh ─────────────────────────────────────────
# Replace the LAN-IP-based OLLAMA_REMOTE_* entries with mesh IPs.
# Format: http://HOST:PORT:WEIGHT
OLLAMA_REMOTE_PHOENIX=http://100.123.227.178:11434:80
OLLAMA_REMOTE_NUK1=http://<nuk1-mesh-ip>:11434:60
OLLAMA_REMOTE_LAB1=http://<lab1-mesh-ip>:11434:40
OLLAMA_REMOTE_LAB2=http://<lab2-mesh-ip>:11434:40
OLLAMA_REMOTE_LAB3=http://<lab3-mesh-ip>:11434:40
OLLAMA_REMOTE_LAB4=http://<lab4-mesh-ip>:11434:40

# Subnet for discover-herd.sh to scan in addition to the LAN:
NETBIRD_SUBNET=100.64.0.0/10
EOF

# Regenerate Olla config from .env and restart
bash scripts/generate-olla-config.sh
sudo systemctl restart ai-stack.service

# Verify Olla sees all peers via mesh
curl http://localhost:40114/internal/status/endpoints | jq .
```

Update `SHEPHERD_PEERS` in the shepherd-control config similarly so the
dashboard polls peers by their mesh IPs:

```bash
# In ~/ai-stack/shepherd/.env or equivalent
SHEPHERD_PEERS=cluster-llm=http://localhost:40118,phoenix=http://100.123.227.178:40118,nuk1=http://<nuk1-mesh-ip>:40118,lab1=http://<lab1-mesh-ip>:40118,lab2=http://<lab2-mesh-ip>:40118,lab3=http://<lab3-mesh-ip>:40118,lab4=http://<lab4-mesh-ip>:40118
```

Restart shepherd-control to pick up:

```bash
sudo systemctl restart shepherd-control   # or however it's launched on cluster-llm
```

## Verification probes

Run these from cluster-llm (or any tailnet peer):

```bash
# 1. Every peer responds to a NetBird ping by its mesh name
for PEER in cluster-llm phoenix nuk1 lab1 lab2 lab3 lab4; do
    netbird ping "$PEER" 2>&1 | head -3
done

# 2. Olla federation health (post-restart)
curl -s http://localhost:40114/internal/status/endpoints | jq '.endpoints[] | {name, healthy, url}'

# 3. Shepherd-control dashboard reflects every peer
curl -s http://localhost:40117/herd/aggregate | jq '.nodes[] | {name, reachable, data_quality}'

# 4. From the dev box / hub page (PR #41):
#    Open http://100.123.141.125:40117/ in a browser
#    Expected: hub-page shows green badges for all reachable services;
#    /herd dashboard shows a card for every peer.
```

## Troubleshooting

### `netbird up` says "tunnel started" but `netbird status` shows disconnected

Almost always firewall — the WireGuard data-plane UDP port can't reach NetBird's
relays. Try:

```bash
sudo netbird status detail
# Look for: "Relayed: yes" — means at least the relay path works.
```

If even the relay fails, check outbound UDP isn't being blocked.

### Peer A can't reach peer B but both show connected to NetBird

Check the ACL group assignments — both peers need to be in groups that have
a policy allowing the destination ports. Default policy is deny-all in NetBird.

### `OLLAMA_HOST=0.0.0.0` was set on a peer but Olla still can't reach it

Two things to check:

1. `netbird status` shows the peer connected (without that, no traffic flows)
2. The peer's firewall (`ufw status` / iptables) allows incoming TCP 11434
   from the mesh subnet. Many distros default-deny incoming traffic on the
   `wt0` interface. Add an explicit rule:

```bash
sudo ufw allow in on wt0 to any port 11434 proto tcp
```

### Setup-key already expired before all nodes joined

Setup-keys have an expiry. If only some nodes joined before expiry:

1. Generate a new setup-key in the NetBird admin UI (re-email to the operator
   mailbox per the credential-handoff protocol)
2. Pick up where you left off — already-joined nodes stay connected; only
   non-joined nodes need the new key

### A peer needs to be removed from the mesh

```bash
# On the peer being removed
sudo netbird down
sudo systemctl disable --now netbird

# In the NetBird admin UI: Peers → select peer → Delete
```

## Memory / doctrine references

- `project_garth_mesh_offer` (Vela memory) — standing offer to wire NetBird across the herd
- `reference_herd_ssh_users` (Vela memory) — SSH user per peer; used in commands above
- `project_aistackdeployment` — current herd peer roster + state
- `feedback_credentials_via_keep_vault` — credential-handoff protocol (why setup-key goes via mailbox, not channel)
- `project_enclave_community_tier` — the broader "herd-as-community-offload" mission this mesh enables

## What this doc does NOT cover (out of scope)

- **Self-hosted NetBird control plane.** Future option if Garth wants to avoid
  the hosted control-plane dependency. Doc to be added when picked up.
- **NetBird Cloud Auth (SSO).** Not needed for the herd; setup-key path
  suffices.
- **WireGuard-without-NetBird fallback.** Lower-priority alternative; doc to
  be added if the hosted path becomes blocking.

When Steve enables Enclave-side herd-offloading (per `5b3a726b` directive),
this mesh is the substrate it routes through — see ai-stack PR #41 hub-page
for the operator-facing entry point.
