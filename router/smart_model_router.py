#!/usr/bin/env python3
"""
Smart Model Router for Olla
Uses qwen2.5:1.5b as a classifier to select the best model for each query.
Sits between OpenCode and Olla: OpenCode -> Smart Router -> Olla -> ollama-arc

Routing:
- Diagnostics      -> qwen2.5:1.5b     (small, fast for sysadmin)
- Scripting/Code   -> qwen2.5-coder:14b (code generation)
- Reasoning        -> deepseek-r1:14b   (chain-of-thought, no tools)
- Longform/Logs    -> gemma3:12b        (long context, summaries, no tools)
- Heavy lifting    -> gemma3:12b        (complex analysis, large context, no tools)
- Tool calling     -> llama3.1:8b       (strong function calling)
- Default          -> qwen2.5:1.5b      (general conversation)

Observable gestalt:
- GET /gestalt/status  — live cluster state (nodes, models, routing table, stats)
- GET /gestalt/events  — SSE stream of routing decisions
- GET /gestalt/ui      — real-time dashboard (D3 force graph)
"""

import asyncio
import json
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse

OLLA_URL = os.environ.get("OLLA_URL", "http://olla:40114")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "40115"))
CAPABILITY_REFRESH_INTERVAL = int(os.environ.get("CAPABILITY_REFRESH_INTERVAL", "300"))

# ── Profile-based routing ─────────────────────────────────────────────────────
# Set ROUTER_PROFILE in .env to switch between profiles.
# Each profile maps query categories to (model, cloud) pairs.
# cloud=True  → Anthropic API (ANTHROPIC_API_KEY required)
# cloud=False → Olla herd (all registered nodes, load-balanced)
#
# Profiles:
#   full-cloud   — all Anthropic; best quality, uses cloud quota
#   hybrid       — cloud for reasoning/planning, local herd for tasks/chat
#   full-local   — all Olla herd; no cloud calls
#
# The fourth profile (phoenix-offline) is handled by pointing OpenCode at
# localhost:11434 directly — no router involved.

ROUTER_PROFILE = os.environ.get("ROUTER_PROFILE", "hybrid")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")

# Marker key stripped from body before forwarding to backend
_CLOUD_ROUTE = "__cloud__"

PROFILES: dict[str, dict[str, dict]] = {
    "full-cloud": {
        "scripting": {"model": "claude-haiku-4-5",  "cloud": True},
        "reasoning": {"model": "claude-sonnet-4-6", "cloud": True},
        "longform":  {"model": "claude-haiku-4-5",  "cloud": True},
        "heavy":     {"model": "claude-sonnet-4-6", "cloud": True},
        "tools":     {"model": "claude-haiku-4-5",  "cloud": True},
        "default":   {"model": "claude-haiku-4-5",  "cloud": True},
    },
    "hybrid": {
        "scripting": {"model": "mistral-small3.2:24b", "cloud": False},
        "reasoning": {"model": "claude-haiku-4-5",     "cloud": True},
        "longform":  {"model": "claude-haiku-4-5",     "cloud": True},
        "heavy":     {"model": "claude-haiku-4-5",     "cloud": True},
        "tools":     {"model": "mistral-small3.2:24b", "cloud": False},
        "default":   {"model": "qwen2.5:7b",           "cloud": False},
    },
    "full-local": {
        "scripting": {"model": "mistral-small3.2:24b", "cloud": False},
        "reasoning": {"model": "deepseek-r1:14b",      "cloud": False},
        "longform":  {"model": "qwen2.5:14b",          "cloud": False},
        "heavy":     {"model": "qwen2.5:14b",          "cloud": False},
        "tools":     {"model": "mistral-small3.2:24b", "cloud": False},
        "default":   {"model": "qwen2.5:7b",           "cloud": False},
    },
}

# Legacy per-model env var overrides (used when ROUTER_PROFILE is not set or unknown)
MODELS = {
    "scripting": os.environ.get("ROUTER_SCRIPTING_MODEL", "qwen2.5-coder:14b"),
    "reasoning": os.environ.get("ROUTER_REASONING_MODEL", "deepseek-r1:14b"),
    "longform": os.environ.get("ROUTER_LONGFORM_MODEL", "gemma3:12b"),
    "heavy": os.environ.get("ROUTER_HEAVY_MODEL", "gemma3:12b"),
    # mistral-small3.2:24b: strong tool-calling, fits 3090 Ti with headroom for KV cache
    "tools": os.environ.get("ROUTER_TOOLS_MODEL", "mistral-small3.2:24b"),
    "default": os.environ.get("ROUTER_DEFAULT_MODEL", "qwen2.5:7b"),
}

# Model families known to support / not support tool calling.
# Used as fallback when the Olla API is unavailable.
_TOOL_CAPABLE_FAMILIES = {
    "mistral",
    "llama3",
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "qwen2.5",
    "qwen3",
    "phi3.5",
    "phi4",
    "command-r",
    "aya",
    "granite3",
    "nemotron",
    "hermes",
}
_TOOL_INCAPABLE_FAMILIES = {
    "deepseek-r1",
    "deepseek-r2",
    "gemma",
    "gemma2",
    "gemma3",
    "gemma4",
    "nomic",
    "mxbai",
    "snowflake",
    "all-minilm",
}

_CLASSIFY_MODEL = os.environ.get("CLASSIFY_MODEL", "qwen2.5:1.5b")

_CLASSIFY_SYSTEM_PROMPT = (
    "Categorize this user message into EXACTLY ONE category:\n"
    "- scripting: code, bash, yaml, config, debugging, automation — asking to write or run code\n"
    "- reasoning: analysis, explanation, comparison, architecture, math — complex analytical tasks\n"
    "- longform: summarization, document writing, editing, reports — extended writing tasks\n"
    "- default: general conversation, questions about files/directories, asking about the system, or anything else\n"
    "IMPORTANT: Questions about files, directories, paths, or the local environment are ALWAYS 'default'.\n"
    "Simple greetings like 'hi' are ALWAYS 'default'.\n"
    "Reply with ONLY the category name."
)

# ── Observable gestalt state ──────────────────────────────────────────────────
_routing_log: deque = deque(maxlen=100)
_request_count: int = 0
_event_queues: list[asyncio.Queue] = []


@dataclass
class ModelCapabilities:
    name: str
    tools: bool
    available: bool = True


class CapabilityRegistry:
    """
    Discovers which models are loaded in Olla and their tool-calling capability.

    On startup and periodically, queries Olla's /v1/models endpoint to build a
    live list of available models. Tool support is inferred from the model family
    name; this avoids requiring a separate /api/show call per model.

    If Olla is unreachable, the registry falls back to the static MODELS dict —
    routing still works, it just won't know about availability.
    """

    def __init__(self):
        self._registry: dict[str, ModelCapabilities] = {}
        self._last_refresh: float = 0.0

    @staticmethod
    def _infer_tools(model_name: str) -> bool:
        base = model_name.split(":")[0].lower()
        for family in _TOOL_INCAPABLE_FAMILIES:
            if base.startswith(family) or family in base:
                return False
        for family in _TOOL_CAPABLE_FAMILIES:
            if base.startswith(family) or family in base:
                return True
        return True  # optimistic default for unknown families

    async def refresh(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(f"{OLLA_URL}/olla/ollama/v1/models")
                resp.raise_for_status()
                data = resp.json()
                models = data.get("data", [])
                fresh: dict[str, ModelCapabilities] = {}
                for m in models:
                    name = m.get("id", "")
                    if name:
                        fresh[name] = ModelCapabilities(
                            name=name,
                            tools=self._infer_tools(name),
                            available=True,
                        )
                self._registry = fresh
                self._last_refresh = time.monotonic()
                print(
                    f"[SmartRouter] Capability registry refreshed — {len(fresh)} models: {', '.join(fresh)}"
                )
        except Exception as exc:
            print(f"[SmartRouter] Capability refresh failed ({exc}) — routing with static MODELS fallback")

    def supports_tools(self, model_name: str) -> bool:
        cap = self._registry.get(model_name)
        return cap.tools if cap else self._infer_tools(model_name)

    def is_available(self, model_name: str) -> bool:
        if not self._registry:
            return True  # no data yet — assume available
        return model_name in self._registry

    def best_tools_model(self) -> str | None:
        """Return the best available model that supports tool calling.
        Prefers known-good tool-using models (≥7B) over arbitrary registry order
        so the fallback path picks a model large enough to actually use tools.
        """
        # Preferred order — proven tool-using models, best first.
        # Ollama container has full NVIDIA GPU access (RTX 3090 Ti, 24 GB VRAM).
        # mistral-small3.2:24b is the only verified local tool model (~20 GiB VRAM).
        preferred = [
            "mistral-small3.2:24b",  # ~20 GiB VRAM — only verified local tool model
            "qwen2.5:14b",           # ~9 GiB VRAM
            "qwen2.5-coder:14b",     # ~9 GiB VRAM
            "qwen2.5:7b",            # ~5 GiB VRAM
            "llama3.1:8b",           # ~6 GiB VRAM
            "llama3.1:latest",
            "mistral:7b",
        ]
        for name in preferred:
            cap = self._registry.get(name)
            if cap and cap.tools and cap.available:
                return name
        # Fall back to any tool-capable model in the registry
        for name, cap in self._registry.items():
            if cap.tools and cap.available:
                return name
        return None

    def best_available(self, exclude: str = "") -> str | None:
        """Return any available model, optionally excluding a specific name."""
        for name in self._registry:
            if name != exclude:
                return name
        return None

    @property
    def stale(self) -> bool:
        return (time.monotonic() - self._last_refresh) > CAPABILITY_REFRESH_INTERVAL


registry = CapabilityRegistry()


async def _periodic_refresh():
    while True:
        await asyncio.sleep(CAPABILITY_REFRESH_INTERVAL)
        if registry.stale:
            await registry.refresh()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await registry.refresh()
    task = asyncio.create_task(_periodic_refresh())
    yield
    task.cancel()


async def classify(text: str) -> tuple[str, str]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                f"{OLLA_URL}/olla/ollama/v1/chat/completions",
                json={
                    "model": _CLASSIFY_MODEL,
                    "messages": [
                        {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                        {"role": "user", "content": text[:1000]},
                    ],
                    "temperature": 0,
                    "max_tokens": 10,
                },
            )
            resp.raise_for_status()
            category = resp.json()["choices"][0]["message"]["content"].strip().lower()
            if category in MODELS:
                return MODELS[category], category
    except Exception as exc:
        print(f"[SmartRouter] Classifier call failed ({exc}) — using default")
    return MODELS["default"], "default"


def _parse_body(body: bytes) -> dict | None:
    """Parse request body once. Returns None on invalid JSON."""
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


def should_route(data: dict, path: str) -> bool:
    """Only route chat completion requests for local models."""
    if not path.startswith("v1/chat/completions"):
        return False
    model = data.get("model", "")
    return not any(c in model for c in ("claude", "gemini", "gpt"))


async def _push_event(event: dict) -> None:
    """Broadcast an event to all active SSE subscribers."""
    dead = []
    for q in _event_queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _event_queues.remove(q)
        except ValueError:
            pass


async def handle_request(data: dict) -> dict:
    global _request_count

    messages = data.get("messages", [])
    if not messages:
        return data

    user_message = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                user_message = content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        user_message = part.get("text", "")
                        break
            break

    if not user_message:
        return data

    needs_tools = bool(data.get("tools") or data.get("functions"))

    # classify() returns (model_name, category_str)
    # model_name = MODELS[category], category_str = "scripting"|"reasoning"|"longform"|"default"
    model, category = await classify(user_message)

    # ── Profile-aware routing ─────────────────────────────────────────────────
    profile = PROFILES.get(ROUTER_PROFILE)
    if profile:
        route = profile.get(category, profile["default"])
        model = route["model"]
        is_cloud = route["cloud"]

        # Cloud models (Claude etc.) handle tools natively — no local fallback needed
        if is_cloud:
            if not ANTHROPIC_API_KEY:
                print(f"[SmartRouter] Profile={ROUTER_PROFILE}: cloud route requested but "
                      f"ANTHROPIC_API_KEY not set — falling back to local default")
                model = MODELS["default"]
                is_cloud = False
            else:
                data["model"] = model
                data[_CLOUD_ROUTE] = True
                print(f"[SmartRouter] [{ROUTER_PROFILE}] '{user_message[:60]}' "
                      f"-> ☁ {model} ({category})")
                _record_route(user_message, model, f"cloud/{category}")
                return data

        # Local route: apply tool-capability checks against Olla registry
        if needs_tools and not registry.supports_tools(model):
            fallback = registry.best_tools_model() or MODELS["tools"]
            print(f"[SmartRouter] [{ROUTER_PROFILE}] {model} no tools → {fallback}")
            model = fallback
            category = f"tools-fallback ({category})"

        if not registry.is_available(model):
            fallback = registry.best_available(exclude=model) or MODELS["default"]
            print(f"[SmartRouter] [{ROUTER_PROFILE}] {model} unavailable → {fallback}")
            model = fallback
            category = f"fallback ({category})"

        data["model"] = model
        print(f"[SmartRouter] [{ROUTER_PROFILE}] '{user_message[:60]}' -> ⬡ {model} ({category})")
        _record_route(user_message, model, category)
        return data

    # ── Legacy routing (no profile or unknown profile) ────────────────────────
    reason = category  # category_str used as reason label in legacy mode

    # Only force the tools model when tools are needed for the task
    if needs_tools and reason == "scripting":
        preferred = MODELS["tools"]
        if not registry.supports_tools(preferred) or not registry.is_available(preferred):
            fallback = registry.best_tools_model()
            if fallback:
                print(f"[SmartRouter] {preferred} not suitable for tools, using {fallback}")
                preferred = fallback
            else:
                print(
                    f"[SmartRouter] WARNING: no tool-capable model available; "
                    f"sending {preferred} anyway (may fail)"
                )
        model, reason = preferred, f"tools ({reason})"

    if needs_tools and not registry.supports_tools(model):
        fallback = registry.best_tools_model() or MODELS["default"]
        print(f"[SmartRouter] {model} does not support tools, falling back to {fallback}")
        model, reason = fallback, f"tools-fallback ({reason})"

    if not registry.is_available(model):
        fallback = registry.best_available(exclude=model) or MODELS["default"]
        print(f"[SmartRouter] {model} not available, falling back to {fallback}")
        model, reason = fallback, f"fallback ({reason})"

    data["model"] = model
    print(f"[SmartRouter] '{user_message[:80]}' -> {model} ({reason})")

    _record_route(user_message, model, reason)
    return data


def _record_route(query: str, model: str, reason: str) -> None:
    """Record a routing decision to the log and SSE event stream."""
    global _request_count
    _request_count += 1
    entry = {
        "ts": time.time(),
        "query": query[:80],
        "model": model,
        "reason": reason,
    }
    _routing_log.appendleft(entry)
    asyncio.create_task(_push_event({"type": "route", **entry}))


# ── Gestalt cluster status ────────────────────────────────────────────────────


async def _fetch_olla_endpoints() -> list[dict]:
    """Query Olla's internal status endpoint for known nodes."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{OLLA_URL}/internal/status/endpoints")
            if resp.status_code == 200:
                data = resp.json()
                # Olla returns various shapes — normalise to a list of node dicts
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("endpoints", data.get("nodes", []))
    except Exception:
        pass
    return []


# ── Gestalt dashboard HTML ────────────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Enclave AI Cluster</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #e6edf3; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

  #header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 20px; display: flex; align-items: center; gap: 24px; flex-shrink: 0; }
  #header h1 { font-size: 15px; font-weight: 600; color: #58a6ff; letter-spacing: 0.5px; }
  .stat { display: flex; flex-direction: column; align-items: center; }
  .stat-value { font-size: 20px; font-weight: 700; color: #3fb950; }
  .stat-label { font-size: 10px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 1px; }
  #status-dot { width: 8px; height: 8px; border-radius: 50%; background: #3fb950; margin-left: auto; box-shadow: 0 0 6px #3fb950; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

  #main { display: flex; flex: 1; overflow: hidden; }
  #graph-panel { flex: 1; position: relative; overflow: hidden; }
  #graph-panel svg { width: 100%; height: 100%; }

  #side-panel { width: 320px; border-left: 1px solid #30363d; display: flex; flex-direction: column; overflow: hidden; }
  #models-panel { padding: 12px; border-bottom: 1px solid #30363d; }
  #models-panel h2 { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px; }
  .model-chip { display: inline-block; background: #1f2937; border: 1px solid #374151; border-radius: 4px; padding: 2px 7px; margin: 2px; font-size: 11px; color: #9ca3af; transition: all 0.3s; }
  .model-chip.tools { border-color: #f59e0b44; color: #f59e0b; }
  .model-chip.active { border-color: #3fb950; color: #3fb950; background: #0d2818; box-shadow: 0 0 8px #3fb95044; }

  #log-panel { flex: 1; overflow-y: auto; padding: 12px; }
  #log-panel h2 { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px; position: sticky; top: 0; background: #0d1117; padding-bottom: 4px; }
  .log-entry { padding: 6px 8px; margin-bottom: 4px; border-left: 2px solid #30363d; background: #161b22; border-radius: 0 4px 4px 0; transition: border-color 0.5s; }
  .log-entry.new { border-left-color: #3fb950; animation: fadeIn 0.4s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateX(-4px); } to { opacity: 1; transform: none; } }
  .log-model { color: #58a6ff; font-weight: 600; font-size: 12px; }
  .log-reason { color: #8b949e; font-size: 10px; margin-top: 1px; }
  .log-query { color: #e6edf3; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 11px; }

  .node circle { stroke-width: 2; cursor: pointer; transition: r 0.3s; }
  .node text { font-size: 11px; fill: #e6edf3; text-anchor: middle; pointer-events: none; }
  .node.hub circle { stroke: #58a6ff; fill: #0d2040; }
  .node.model circle { stroke: #30363d; fill: #161b22; }
  .node.model.tools circle { stroke: #f59e0b44; }
  .node.active circle { stroke: #3fb950 !important; fill: #0d2818 !important; }
  .link { stroke: #30363d; stroke-opacity: 0.5; stroke-width: 1; }
  .link.active { stroke: #3fb950; stroke-opacity: 0.8; stroke-width: 2; animation: linkPulse 0.6s ease-out; }
  @keyframes linkPulse { from { stroke-opacity: 1; stroke-width: 3; } to { stroke-opacity: 0.8; stroke-width: 2; } }

  #footer { background: #161b22; border-top: 1px solid #30363d; padding: 6px 20px; font-size: 10px; color: #8b949e; flex-shrink: 0; }
</style>
</head>
<body>
<div id="header">
  <h1>⬡ ENCLAVE AI CLUSTER</h1>
  <div class="stat"><div class="stat-value" id="stat-nodes">—</div><div class="stat-label">Nodes</div></div>
  <div class="stat"><div class="stat-value" id="stat-models">—</div><div class="stat-label">Models</div></div>
  <div class="stat"><div class="stat-value" id="stat-requests">0</div><div class="stat-label">Requests</div></div>
  <div class="stat"><div class="stat-value" id="stat-tools">—</div><div class="stat-label">Tool-capable</div></div>
  <div id="status-dot" title="Live"></div>
</div>
<div id="main">
  <div id="graph-panel"><svg id="graph"></svg></div>
  <div id="side-panel">
    <div id="models-panel">
      <h2>Available Models</h2>
      <div id="model-chips"></div>
    </div>
    <div id="log-panel">
      <h2>Routing Log</h2>
      <div id="log-entries"></div>
    </div>
  </div>
</div>
<div id="footer">Enclave Smart Router · <span id="router-url"></span> · SSE live feed active</div>

<script>
const ROUTER = window.location.origin;
document.getElementById('router-url').textContent = ROUTER;

// ── D3 force graph ────────────────────────────────────────────────────────────
const svg = d3.select('#graph');
let width = 0, height = 0;
const g = svg.append('g');

// Zoom + pan
svg.call(d3.zoom().scaleExtent([0.3, 3]).on('zoom', e => g.attr('transform', e.transform)));

let simulation, linkSel, nodeSel;
let graphNodes = [], graphLinks = [];
let activeModel = null;

function initGraph(models) {
  width = document.getElementById('graph-panel').clientWidth;
  height = document.getElementById('graph-panel').clientHeight;

  graphNodes = [{ id: 'router', type: 'hub', label: 'Router', x: width/2, y: height/2, fx: width/2, fy: height/2 }];
  graphLinks = [];

  models.forEach(m => {
    graphNodes.push({ id: m.name, type: 'model', label: m.name.split(':')[0], tools: m.tools });
    graphLinks.push({ source: 'router', target: m.name });
  });

  simulation = d3.forceSimulation(graphNodes)
    .force('link', d3.forceLink(graphLinks).id(d => d.id).distance(120).strength(0.5))
    .force('charge', d3.forceManyBody().strength(-200))
    .force('collision', d3.forceCollide(40))
    .on('tick', ticked);

  g.selectAll('*').remove();

  linkSel = g.append('g').selectAll('line')
    .data(graphLinks).join('line').attr('class', 'link');

  nodeSel = g.append('g').selectAll('g')
    .data(graphNodes).join('g')
    .attr('class', d => `node ${d.type}${d.tools ? ' tools' : ''}`)
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => { if (!e.active) simulation.alphaTarget(0); if (d.type !== 'hub') { d.fx = null; d.fy = null; } }));

  nodeSel.append('circle').attr('r', d => d.type === 'hub' ? 24 : 16);
  nodeSel.append('text').attr('dy', d => d.type === 'hub' ? 36 : 28).text(d => d.label);
}

function ticked() {
  linkSel.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
         .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
  nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
}

function activateModel(modelName) {
  if (!nodeSel) return;
  nodeSel.classed('active', d => d.id === modelName || d.id === 'router');
  linkSel.classed('active', d => d.target.id === modelName || d.target === modelName);
  setTimeout(() => {
    nodeSel.classed('active', false);
    linkSel.classed('active', false);
  }, 1200);
}

// ── Status polling ────────────────────────────────────────────────────────────
let lastModelCount = 0;

async function refreshStatus() {
  try {
    const r = await fetch(`${ROUTER}/gestalt/status`);
    const d = await r.json();

    document.getElementById('stat-nodes').textContent = d.nodes.length || 1;
    document.getElementById('stat-models').textContent = d.totals.models_available;
    document.getElementById('stat-requests').textContent = d.totals.requests_served;
    document.getElementById('stat-tools').textContent = d.models.filter(m => m.tools).length;

    if (d.totals.models_available !== lastModelCount) {
      lastModelCount = d.totals.models_available;
      initGraph(d.models);
      renderChips(d.models);
    }

    if (d.routing.recent_decisions.length > 0) {
      renderLog(d.routing.recent_decisions);
    }
  } catch(e) { /* olla unreachable */ }
}

function renderChips(models) {
  const el = document.getElementById('model-chips');
  el.innerHTML = models.map(m =>
    `<span class="model-chip${m.tools ? ' tools' : ''}" data-model="${m.name}">${m.name}</span>`
  ).join('');
}

function renderLog(decisions) {
  const el = document.getElementById('log-entries');
  const existing = new Set(Array.from(el.querySelectorAll('.log-entry')).map(e => e.dataset.ts));
  decisions.slice(0, 20).forEach(d => {
    if (existing.has(String(d.ts))) return;
    const div = document.createElement('div');
    div.className = 'log-entry new';
    div.dataset.ts = d.ts;
    div.innerHTML = `<div class="log-model">${d.model}</div><div class="log-reason">${d.reason}</div><div class="log-query">${d.query}</div>`;
    el.insertBefore(div, el.firstChild);
    setTimeout(() => div.classList.remove('new'), 600);
  });
  while (el.children.length > 30) el.removeChild(el.lastChild);
}

// ── SSE live feed ─────────────────────────────────────────────────────────────
function connectSSE() {
  const es = new EventSource(`${ROUTER}/gestalt/events`);
  es.onmessage = e => {
    const data = JSON.parse(e.data);
    if (data.type === 'route') {
      document.getElementById('stat-requests').textContent =
        parseInt(document.getElementById('stat-requests').textContent) + 1;

      activateModel(data.model);

      // Update chip highlight
      document.querySelectorAll('.model-chip').forEach(c => {
        c.classList.toggle('active', c.dataset.model === data.model);
        setTimeout(() => c.classList.remove('active'), 1200);
      });

      // Prepend log entry
      const el = document.getElementById('log-entries');
      const div = document.createElement('div');
      div.className = 'log-entry new';
      div.dataset.ts = data.ts;
      div.innerHTML = `<div class="log-model">${data.model}</div><div class="log-reason">${data.reason}</div><div class="log-query">${data.query}</div>`;
      el.insertBefore(div, el.firstChild);
      setTimeout(() => div.classList.remove('new'), 600);
      while (el.children.length > 30) el.removeChild(el.lastChild);
    }
  };
  es.onerror = () => setTimeout(connectSSE, 3000);
}

// ── Init ──────────────────────────────────────────────────────────────────────
refreshStatus();
setInterval(refreshStatus, 5000);
connectSSE();
new ResizeObserver(() => {
  if (lastModelCount > 0) { width = document.getElementById('graph-panel').clientWidth; height = document.getElementById('graph-panel').clientHeight; if (simulation) { simulation.force('center', d3.forceCenter(width/2, height/2)).alpha(0.3).restart(); }}
}).observe(document.getElementById('graph-panel'));
</script>
</body>
</html>"""


app = FastAPI(title="Smart Model Router", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "models_loaded": len(registry._registry),
        "registry_stale": registry.stale,
    }


@app.get("/v1/models")
async def list_models():
    """Return available models from Olla."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{OLLA_URL}/olla/ollama/v1/models")
        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))


@app.get("/v1/router/capabilities")
async def list_capabilities():
    """Return the current capability registry — useful for debugging routing decisions."""
    await registry.refresh()
    return {
        "models": [
            {"name": cap.name, "tools": cap.tools, "available": cap.available}
            for cap in registry._registry.values()
        ],
        "preferred_tools_model": registry.best_tools_model(),
        "configured_models": MODELS,
    }


@app.get("/gestalt/status")
async def gestalt_status():
    """Live cluster state: nodes, models, routing table, recent decisions."""
    nodes = await _fetch_olla_endpoints()
    return {
        "timestamp": time.time(),
        "nodes": nodes,
        "models": [
            {"name": cap.name, "tools": cap.tools, "available": cap.available}
            for cap in registry._registry.values()
        ],
        "routing": {
            "configured_models": MODELS,
            "recent_decisions": list(_routing_log),
        },
        "totals": {
            "requests_served": _request_count,
            "models_available": len(registry._registry),
        },
    }


@app.get("/gestalt/events")
async def gestalt_events():
    """SSE stream of live routing decisions."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _event_queues.append(queue)

    async def generate():
        try:
            # Send a keepalive comment every 15s so the connection stays open
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            try:
                _event_queues.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/gestalt/ui", response_class=HTMLResponse)
async def gestalt_ui():
    """Real-time cluster dashboard — D3 force graph + routing log."""
    return _DASHBOARD_HTML


async def _proxy_to_anthropic(
    client: httpx.AsyncClient,
    path: str,
    body: bytes,
) -> Response:
    """Forward a chat completion request to Anthropic's OpenAI-compatible API."""
    url = f"{ANTHROPIC_BASE_URL}/{path}"
    headers = {
        "Authorization": f"Bearer {ANTHROPIC_API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    try:
        response = await client.request(
            method="POST",
            url=url,
            headers=headers,
            content=body,
        )
        print(f"[SmartRouter] Anthropic responded HTTP {response.status_code}")
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    except Exception as e:
        print(f"[SmartRouter] Anthropic request failed: {e}")
        return Response(
            content=json.dumps({"error": f"Anthropic request failed: {e}"}).encode(),
            status_code=503,
            headers={"Content-Type": "application/json"},
        )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(request: Request, path: str):
    if path.startswith("v1/"):
        raw_body = await request.body()
        body = raw_body
        is_cloud = False

        if raw_body:
            data = _parse_body(raw_body)
            if data and should_route(data, path):
                routed = await handle_request(data)
                # Extract and remove the cloud-route marker before forwarding
                is_cloud = routed.pop(_CLOUD_ROUTE, False)
                body = json.dumps(routed).encode()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        ) as client:

            # Cloud route: forward to Anthropic
            if is_cloud and ANTHROPIC_API_KEY:
                return await _proxy_to_anthropic(client, path, body)

            # Local route: forward to Olla herd
            url = f"{OLLA_URL}/olla/ollama/{path}"
            headers = dict(request.headers)
            headers.pop("host", None)
            headers.pop("content-length", None)

            response = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=dict(request.query_params),
            )

            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
            )

    return Response(status_code=404)


def main():
    import uvicorn

    print(f"[SmartRouter] Listening on {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"[SmartRouter] Forwarding to Olla at {OLLA_URL}")
    print(f"[SmartRouter] Profile: {ROUTER_PROFILE} "
          f"({'known' if ROUTER_PROFILE in PROFILES else 'UNKNOWN — using legacy routing'})")
    print(f"[SmartRouter] Cloud routing: {'enabled (Anthropic)' if ANTHROPIC_API_KEY else 'disabled (no ANTHROPIC_API_KEY)'}")
    print(f"[SmartRouter] Capability refresh interval: {CAPABILITY_REFRESH_INTERVAL}s")
    print(f"[SmartRouter] Dashboard: http://{LISTEN_HOST}:{LISTEN_PORT}/gestalt/ui")
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
