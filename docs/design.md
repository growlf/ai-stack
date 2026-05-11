# Architecture and design decisions

## Stack overview

```
OpenCode (CLI + Obsidian sidebar plugin)
  |
  |--- tool: retriever :42000  (vault RAG)
  |       FastAPI + sqlite-vec + watchdog
  |       hybrid search: FTS5 + vector (sqlite-vec)
  |       embeds via Olla → ollama (nomic-embed-text)
  |       vault mounted read-only at /vault
  |
  |--- provider: smart-router :40115  (model selection)
  |       |--- Olla :40114  (unified LLM router + load balancer)
  |               |--- ollama :11434  (local inference)
  |               |--- litellm :4000     (cloud API gateway: Claude, Gemini)
  |               |--- OLLAMA_REMOTE_*   (LAN nodes, optional)
  |
  |--- provider: litellm :4000  (direct, optional)

discoverer (systemd timer): mDNS scan → updates Olla config + OpenCode providers
```

## Why OpenCode over Open WebUI

OpenCode provides a better development experience than Open WebUI for the target use case (code, configs, terminal work):
- Runs natively in the terminal and as an Obsidian sidebar plugin
- Tool use (vault search, shell commands) is first-class
- No persistent browser tab required

Open WebUI was removed. Chat history volume and the admin panel were the only losses.

## Why retriever (sqlite-vec) over Khoj

Khoj required PostgreSQL and a full web service stack to provide Obsidian RAG. The retriever replaces it with:
- SQLite + sqlite-vec: no separate database process, file-based persistence
- FTS5 hybrid search: BM25 keyword + vector similarity, RRF fusion
- Watchdog: incremental indexing on vault file changes, no polling
- ~100MB footprint vs. Khoj's several GB

The trade-off: no Khoj web UI or its Obsidian plugin. OpenCode's vault-search tool covers the use case directly.

## Why Olla for routing

Olla provides unified routing and load balancing across local Ollama nodes and the LiteLLM proxy. A single endpoint (`localhost:40114`) covers all models — local and cloud — without OpenCode needing separate provider configs per machine.

## Smart router placement

The smart router (`:40115`) sits in front of Olla. It classifies requests and selects models before Olla sees them. This keeps Olla's job simple (load balancing, failover) and puts routing intelligence in one place.

The router is on the critical path for every request. Design constraint: per-request overhead must be sub-millisecond. The current implementation (compiled regex, in-memory capability registry, single JSON parse) meets this. See [docs/smart-router.md](smart-router.md) for the full latency profile.

## Multi-machine architecture

The stack is designed to span multiple machines. Olla aggregates local Ollama nodes and LAN-discovered nodes under one endpoint. `discover-herd.sh` handles mDNS discovery and config generation.

The stretch goal is NetBird VPN integration — automatic WireGuard mesh between machines, allowing discovery and resource sharing over the internet, not just LAN. The foundation (Olla multi-node routing, `discover-network.sh` subnet scanning) is in place. NetBird adds the secure tunnel layer.

## Services removed

| Service | Replaced by | Reason |
|---------|------------|--------|
| Open WebUI | OpenCode | Better dev experience, no persistent browser tab |
| Pipelines | — | OpenWebUI-only |
| Open Terminal | — | Browser terminal, integrated with WebUI only |
| Khoj + PostgreSQL | retriever (sqlite-vec) | Heavy for single-user RAG; sqlite-vec is sufficient |
