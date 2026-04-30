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

### Making changes

1. **Fork** the repository and create a branch from `main`:
   ```bash
   git checkout -b fix/my-fix
   ```

2. **Make your changes.** Keep commits focused and atomic.

3. **Validate** before opening a PR:
   ```bash
   # Validate docker-compose.yml
   docker compose config

   # Lint shell scripts
   shellcheck scripts/*.sh install.sh post-install.sh
   ```

4. **Never commit real credentials.** Use placeholder values like `changeme` in examples.

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
- Python (pipelines / tools): follow PEP 8; add a module-level docstring

## Reporting security issues

Please **do not** open a public issue for security vulnerabilities.  
See [SECURITY.md](SECURITY.md) for the responsible disclosure process.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).  
Please be respectful and constructive in all interactions.
