# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- GitHub Actions CI workflow (docker-compose validation, shellcheck)
- GitHub Actions Release workflow (auto-release on version tags)
- Pull Request template
- Issue templates (bug report, feature request)
- CODEOWNERS file
- Dependabot configuration for GitHub Actions
- CONTRIBUTING.md guide
- SECURITY.md policy
- CODE_OF_CONDUCT.md (Contributor Covenant v2.1)
- CHANGELOG.md
- **Retriever service** — lightweight Obsidian vault RAG replacing Khoj + PostgreSQL
  - FastAPI + sqlite-vec (file-based vector store, no separate DB)
  - Hybrid search: FTS5 keyword (BM25) + vector similarity, fused via RRF
  - Watchdog live vault indexing (inotify)
  - API-only: `POST /search`, `POST /reindex`, `GET /health`
  - Embedded via Olla → ollama-arc (nomic-embed-text)
- **discover-herd.sh** — mDNS + subnet scan for auto-discovery of remote Ollama nodes
- **PLANS.md** — design document for stack simplification

### Removed
- **Open WebUI** — replaced by OpenCode (CLI + Obsidian sidebar plugin)
- **Pipelines** — no longer needed (smart_model_router removed)
- **Open Terminal** — no longer needed
- **Khoj / khoj-db** — replaced by retriever service (sqlite-vec, no PostgreSQL)
- **post-install.sh** — entirely targeted Open WebUI API
- **tools/system_diagnostics.py** — Open WebUI tool protocol
- **khoj-sync/** — never implemented CouchDB sync
- All associated environment variables (WEBUI_*, PIPELINES_*, KHOJ_*, COUCHDB_*, etc.)

### Changed
- Stack reduced from 8 services to 4 (ollama-arc, litellm, olla, retriever)
- `.env.example` slimmed down to only active configuration
- `install.sh` updated: no pipelines deployment, no Open WebUI volume, updated completion output
- `AGENTS.md` reflects new architecture

## [0.1.0] - 2026-04-30

### Added
- Initial release: Ollama (Intel Arc iGPU) + Open WebUI + Pipelines + Open Terminal stack
- Smart model router pipeline
- System diagnostics tool
- `install.sh` and `post-install.sh` scripts
- GPU pre-flight script (`check-arc-gpu.sh`)
- systemd service unit (`ai-stack.service`)
- Khoj integration with pgvector for Obsidian RAG
- Documentation: post-install guide, model guide, troubleshooting, Khoj setup

[Unreleased]: https://github.com/growlf/ai-stack/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/growlf/ai-stack/releases/tag/v0.1.0
