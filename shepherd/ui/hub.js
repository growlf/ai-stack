/* hub.js — services-hub landing page for ai-stack
 *
 * Renders quick-link cards for every service running on this host + polls
 * lightweight health-check endpoints so each card shows a live up/down badge.
 *
 * Auto-discovers the host hostname so links work whether you're hitting
 * localhost, a LAN IP, or a VPN-routed mesh hostname.
 */

(function () {
  "use strict";

  // ─── Service catalog ─────────────────────────────────────────────────────
  // Each entry: { id, emoji, name, desc, port, openPath, probePath }
  //   - openPath: where the "Open" link points (user-facing UI route)
  //   - probePath: lightweight endpoint we hit for the up/down badge
  //   - probePath null = skip live probe (e.g., for purely-data APIs where
  //     a missing UI doesn't mean the service is down)
  const SERVICES = [
    {
      id: "shepherd-herd",
      emoji: "🐑",
      name: "Shepherd herd",
      desc: "Live per-node CPU / RAM / GPU + resident models + federation peers across the herd.",
      port: 40117,
      openPath: "/herd",
      probePath: "/healthz",
    },
    {
      id: "olla",
      emoji: "🌍",
      name: "Olla",
      desc: "Federation + load-balancer across herd nodes. Routes requests to the right backend.",
      port: 40114,
      openPath: "/",
      probePath: "/internal/health",
    },
    {
      id: "router",
      emoji: "🧭",
      name: "Smart Router",
      desc: "LLM-based model classifier. Picks the right local model for each request automatically.",
      port: 40115,
      openPath: "/gestalt/ui",
      probePath: "/healthz",
    },
    {
      id: "litellm",
      emoji: "🤖",
      name: "LiteLLM",
      desc: "OpenAI-compatible gateway for cloud models (Claude, Gemini, OpenAI). Admin UI + key management.",
      port: 4000,
      openPath: "/ui",
      probePath: "/health/liveliness",
    },
    {
      id: "retriever",
      emoji: "📚",
      name: "Retriever",
      desc: "Vault indexer + semantic search for RAG. Indexes Obsidian notes (or any markdown vault).",
      port: 42000,
      openPath: "/health",
      probePath: "/health",
    },
    {
      id: "ollama",
      emoji: "🧠",
      name: "Ollama API",
      desc: "Local LLM runtime. Bare API for direct model interaction or testing.",
      port: 11434,
      openPath: "/api/tags",
      probePath: "/api/tags",
    },
  ];

  // ─── Host detection ──────────────────────────────────────────────────────
  // window.location.hostname gives the hostname the user used to reach this
  // page; we use the same one for service links so click-through works from
  // any access path (localhost, LAN IP, mesh hostname).
  const HOST = window.location.hostname || "localhost";

  function serviceURL(svc, path) {
    return `${window.location.protocol}//${HOST}:${svc.port}${path}`;
  }

  // ─── Card rendering ──────────────────────────────────────────────────────
  function renderCard(svc) {
    const card = document.createElement("article");
    card.className = "service-card status-checking";
    card.dataset.serviceId = svc.id;

    card.innerHTML = `
      <div class="service-card-header">
        <div class="service-card-title">
          <span class="service-card-emoji">${svc.emoji}</span>
          <span class="service-card-name">${escapeHTML(svc.name)}</span>
        </div>
        <span class="service-badge checking" data-role="badge">checking…</span>
      </div>
      <div class="service-desc">${escapeHTML(svc.desc)}</div>
      <div class="service-card-footer">
        <span class="service-port">:${svc.port}</span>
        <a class="service-link"
           href="${serviceURL(svc, svc.openPath)}"
           target="_blank" rel="noopener"
        >Open ↗</a>
      </div>
    `;
    return card;
  }

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ─── Health probe ────────────────────────────────────────────────────────
  // Lightweight HEAD/GET with a tight timeout. 2xx/3xx = up, anything else
  // (network failure, timeout, 4xx/5xx) = down. We deliberately don't try
  // to interpret the response body — just "is the endpoint reachable."
  async function probe(svc) {
    if (!svc.probePath) return "skip";
    const url = serviceURL(svc, svc.probePath);
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 3000);
    try {
      const r = await fetch(url, {
        method: "GET",
        cache: "no-store",
        mode: "no-cors",  // many services don't send CORS headers; we just
                          // need to know if the fetch resolves vs throws
        signal: ctrl.signal,
      });
      clearTimeout(timeout);
      // With mode:"no-cors" the response is opaque — status is always 0.
      // A successful opaque response still means the endpoint accepted the
      // connection, which is enough for "up". Real-down cases throw.
      return r ? "up" : "down";
    } catch (e) {
      clearTimeout(timeout);
      return "down";
    }
  }

  function applyStatus(card, status) {
    const badge = card.querySelector('[data-role="badge"]');
    card.classList.remove("status-up", "status-down", "status-checking");
    if (badge) badge.classList.remove("up", "down", "checking");

    if (status === "up") {
      card.classList.add("status-up");
      if (badge) { badge.classList.add("up"); badge.textContent = "up"; }
    } else if (status === "down") {
      card.classList.add("status-down");
      if (badge) { badge.classList.add("down"); badge.textContent = "down"; }
    } else if (status === "skip") {
      card.classList.add("status-up");  // assume okay if we can't probe
      if (badge) { badge.classList.add("up"); badge.textContent = "—"; }
    } else {
      card.classList.add("status-checking");
      if (badge) { badge.classList.add("checking"); badge.textContent = "checking…"; }
    }
  }

  async function refreshAll() {
    const cards = document.querySelectorAll(".service-card");
    const upCount = { up: 0, total: 0 };
    await Promise.all(Array.from(cards).map(async (card) => {
      const svc = SERVICES.find((s) => s.id === card.dataset.serviceId);
      if (!svc) return;
      const status = await probe(svc);
      applyStatus(card, status);
      upCount.total++;
      if (status === "up" || status === "skip") upCount.up++;
    }));
    const banner = document.getElementById("overall-status");
    if (banner) {
      banner.textContent = `${upCount.up} / ${upCount.total} services up`;
      banner.className = "status " + (upCount.up === upCount.total ? "connected" : upCount.up === 0 ? "error" : "degraded");
    }
  }

  // ─── Clock ───────────────────────────────────────────────────────────────
  function updateClock() {
    const el = document.getElementById("clock");
    if (el) el.textContent = new Date().toLocaleTimeString();
  }

  // ─── Boot ────────────────────────────────────────────────────────────────
  function init() {
    const grid = document.getElementById("hub-grid");
    if (!grid) return;
    SERVICES.forEach((svc) => grid.appendChild(renderCard(svc)));
    refreshAll();
    setInterval(refreshAll, 15000);  // poll every 15s
    updateClock();
    setInterval(updateClock, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
