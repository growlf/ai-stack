# Contributing to ai-stack

Thank you for your interest in contributing! This project is built from real homelab experience and community input is very welcome.

## Ways to contribute

- **Report bugs** — open a [bug report](https://github.com/growlf/ai-stack/issues/new?template=bug_report.yml)
- **Request features** — open a [feature request](https://github.com/growlf/ai-stack/issues/new?template=feature_request.yml)
- **Submit pull requests** — fix bugs, improve docs, add hardware support
- **Share your setup** — open a Discussion if you get it running on different hardware

## Development workflow

### Prerequisites

- Docker and Docker Compose installed
- `shellcheck` for linting shell scripts (`sudo apt install shellcheck`)
- `ruff` for Python lint + format (`pip install ruff`)

### Making changes

1. **Fork** the repository and create a branch from `main`:
   ```bash
   git checkout -b fix/my-fix
   ```

2. **Make your changes.** Keep commits focused and atomic.

3. **Pre-flight check before opening a PR** — same checks CI runs, run locally to catch issues before push:
   ```bash
   # Docker compose syntax
   docker compose config

   # Shell scripts
   shellcheck scripts/*.sh install.sh

   # Python lint + format check
   ruff check .
   ruff format --check .

   # .env.example parity (warns if you added an env var to code without
   # updating .env.example — hard-fail in CI)
   python3 scripts/check-env-example.py
   ```

   If you added or removed env vars, update `.env.example` with a placeholder value
   and a comment describing what the variable is for.

4. **Never commit real credentials.**
   - Use `<vaultwarden:org/item>` placeholders for API keys if Bitwarden is configured
   - Never commit a resolved `.env` file (only placeholder values should appear)
   - Use placeholder values like `changeme` in examples

5. **Open a pull request** against `main` and fill in the PR template.

### Commit message style

Use the [Conventional Commits](https://www.conventionalcommits.org/) style:

```
feat: add support for AMD GPU
fix: correct renderD128 device path detection
docs: clarify multi-machine Ollama setup
ci: update shellcheck action to v2
```

## Code style

- Shell scripts: POSIX-compatible where possible; always pass `shellcheck`
- Docker Compose: keep services alphabetically ordered within logical groups
- Python (retriever): follow PEP 8

## Reporting security issues

Please **do not** open a public issue for security vulnerabilities.  
See [SECURITY.md](SECURITY.md) for the responsible disclosure process.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).  
Please be respectful and constructive in all interactions.
