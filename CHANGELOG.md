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
