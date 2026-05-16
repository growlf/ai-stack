// Shepherd kiosk dashboard — live updates from shepherd-control's aggregated view.
// Falls back to direct shepherd-node polling if /herd/aggregate isn't available yet.

const POLL_INTERVAL_MS = 5000;
const SOURCE_URL = window.SHEPHERD_DATA_URL || "/herd/aggregate";

const els = {
  status: document.getElementById("overall-status"),
  clock: document.getElementById("clock"),
  nodes: document.getElementById("nodes"),
  totalVram: document.getElementById("total-vram"),
  totalModels: document.getElementById("total-models"),
  totalNodes: document.getElementById("total-nodes"),
  modeBadge: document.getElementById("mode-badge"),
  footerSource: document.getElementById("footer-source"),
  federationLabel: document.getElementById("federation-label"),
};

els.footerSource.textContent = SOURCE_URL;

function fmtMB(mb) {
  if (mb == null) return "—";
  if (mb < 1024) return `${mb} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

function tickClock() {
  const now = new Date();
  els.clock.textContent = now.toLocaleTimeString("en-US", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}
setInterval(tickClock, 1000);
tickClock();

function classifyHardware(hw) {
  if (!hw) return "cpu";
  if (hw.implementation_status === "implemented" && hw.accelerator_type !== "cpu") return "implemented";
  if (hw.implementation_status === "stub") return "stub";
  return "cpu";
}

function renderNode(node) {
  if (node.data_quality === "lite") return renderLiteCard(node);
  return renderFullCard(node);
}

function renderFullCard(node) {
  const hw = node.hardware || {};
  const vram_used = hw.vram_used_mb;
  const vram_total = hw.vram_total_mb;
  const vram_pct = (vram_used != null && vram_total) ? Math.round(vram_used / vram_total * 100) : 0;
  const util = hw.utilization_pct;
  const sys = node.system || {};
  const ollama = node.ollama || {};
  const models = ollama.resident_models || [];

  const hwClass = classifyHardware(hw);
  let healthClass = "healthy";
  if (!node.reachable) healthClass = "offline";
  else if (node.olla && node.olla.responding === false) healthClass = "degraded";

  const card = document.createElement("div");
  card.className = `node-card ${healthClass}`;
  card.innerHTML = `
    <div class="node-header">
      <div>
        <div class="node-name">${node.name || "(unknown)"}</div>
        <div class="node-role">${node.role || "herd peer"}</div>
      </div>
      <div class="node-hardware ${hwClass}">${hw.accelerator_name || "—"}</div>
    </div>
    <div class="gauges">
      <div class="gauge">
        <div class="gauge-label">VRAM</div>
        <div class="gauge-value">${vram_used != null ? fmtMB(vram_used) : "—"}<span class="unit">${vram_total ? `/ ${fmtMB(vram_total)}` : ""}</span></div>
        <div class="gauge-bar"><div class="gauge-bar-fill" style="width: ${vram_pct}%"></div></div>
      </div>
      <div class="gauge">
        <div class="gauge-label">CPU</div>
        <div class="gauge-value">${sys.cpu_pct != null ? sys.cpu_pct.toFixed(0) : "—"}<span class="unit">%</span></div>
        <div class="gauge-bar"><div class="gauge-bar-fill" style="width: ${sys.cpu_pct || 0}%"></div></div>
      </div>
      <div class="gauge">
        <div class="gauge-label">GPU util</div>
        <div class="gauge-value">${util != null ? util : "—"}<span class="unit">%</span></div>
        <div class="gauge-bar"><div class="gauge-bar-fill" style="width: ${util || 0}%"></div></div>
      </div>
      <div class="gauge">
        <div class="gauge-label">RAM</div>
        <div class="gauge-value">${sys.ram_used_mb != null ? fmtMB(sys.ram_used_mb) : "—"}<span class="unit">${sys.ram_total_mb ? `/ ${fmtMB(sys.ram_total_mb)}` : ""}</span></div>
        <div class="gauge-bar"><div class="gauge-bar-fill" style="width: ${sys.ram_used_mb && sys.ram_total_mb ? Math.round(sys.ram_used_mb / sys.ram_total_mb * 100) : 0}%"></div></div>
      </div>
    </div>
    <div class="resident-models">
      <div class="resident-models-label">Resident models (${models.length})</div>
      <div class="model-list">
        ${models.length === 0
          ? `<span class="no-models">no models warm</span>`
          : models.map(m => `<span class="model-badge warm" title="${m.size_vram_mb || 0} MB on GPU">${m.name}</span>`).join("")}
      </div>
    </div>
  `;
  return card;
}

function renderLiteCard(node) {
  // Lite cards: data from Olla federation only (no shepherd-node sidecar on this peer yet).
  // Surface what Olla DOES know — healthy/offline, model count, model names if directly reachable.
  const ollama = node.ollama || {};
  const olla = node.olla || {};
  const models = ollama.resident_models || [];
  const modelCount = ollama.model_count_via_olla;
  const status = olla.status_via_local_olla || (node.reachable ? "healthy" : "offline");
  const discoveredVia = olla.discovered_via || "";
  const discoveredHost = discoveredVia.replace(/^https?:\/\//, "").split(":")[0];

  let healthClass = node.reachable ? "healthy" : "offline";
  if (status === "warming" || status === "degraded") healthClass = "degraded";

  const card = document.createElement("div");
  card.className = `node-card lite ${healthClass}`;
  card.innerHTML = `
    <div class="node-header">
      <div>
        <div class="node-name">${node.name || "(unknown)"}</div>
        <div class="node-role">lite — Olla federation view</div>
      </div>
      <div class="node-hardware stub">via ${discoveredHost || "Olla"}</div>
    </div>
    <div class="lite-stats">
      <div class="lite-stat">
        <div class="lite-stat-label">status</div>
        <div class="lite-stat-value status-${status}">${status}</div>
      </div>
      <div class="lite-stat">
        <div class="lite-stat-label">models known</div>
        <div class="lite-stat-value big">${modelCount != null ? modelCount : "—"}</div>
      </div>
      <div class="lite-stat">
        <div class="lite-stat-label">warm now</div>
        <div class="lite-stat-value big">${models.length}</div>
      </div>
    </div>
    <div class="resident-models">
      <div class="resident-models-label">${models.length > 0 ? `Resident models (${models.length})` : "Resource stats unavailable"}</div>
      <div class="model-list">
        ${models.length > 0
          ? models.map(m => `<span class="model-badge warm" title="${m.size_vram_mb || 0} MB on GPU">${m.name}</span>`).join("")
          : `<span class="no-models">deploy shepherd-node here for GPU/CPU/RAM</span>`}
      </div>
    </div>
  `;
  return card;
}

function renderAggregate(data) {
  // data shape: { nodes: [...], timestamp: <iso>, source: <str> }
  const nodes = data.nodes || [];
  els.nodes.innerHTML = "";
  nodes.forEach(n => els.nodes.appendChild(renderNode(n)));

  // Totals
  let total_vram_used = 0, total_vram = 0, total_models = 0, online = 0;
  for (const n of nodes) {
    if (n.reachable) online++;
    const hw = n.hardware || {};
    if (hw.vram_used_mb != null) total_vram_used += hw.vram_used_mb;
    if (hw.vram_total_mb != null) total_vram += hw.vram_total_mb;
    total_models += (n.ollama && n.ollama.resident_models || []).length;
  }
  els.totalVram.textContent = `${fmtMB(total_vram_used)} / ${fmtMB(total_vram)}`;
  els.totalModels.textContent = total_models;
  els.totalNodes.textContent = `${online} / ${nodes.length}`;
  els.federationLabel.textContent = nodes.map(n => n.name).join(" · ");

  els.status.textContent = `connected — ${online}/${nodes.length} online`;
  els.status.className = "status " + (online === nodes.length ? "connected" : online === 0 ? "error" : "degraded");
}

async function poll() {
  try {
    const r = await fetch(SOURCE_URL, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderAggregate(data);
  } catch (e) {
    els.status.textContent = `disconnected — ${e.message}`;
    els.status.className = "status error";
  }
}

poll();
setInterval(poll, POLL_INTERVAL_MS);
