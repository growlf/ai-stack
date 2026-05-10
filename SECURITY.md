# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main`  | ✅ Yes     |

Only the current `main` branch receives security fixes.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in this project, please report it via **[GitHub Private Vulnerability Reporting](https://github.com/growlf/ai-stack/security/advisories/new)** so it can be assessed and addressed privately before public disclosure.

When reporting, please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept (if applicable)
- Affected versions or components
- Any suggested mitigations

You can expect an acknowledgement within **72 hours** and a resolution timeline once the issue is confirmed.

## Security considerations for this project

- **Never commit real credentials.** Use `.env` (which is git-ignored) for secrets; `.env.example` must only contain placeholder values.
- **Never commit backup files containing secrets.** Files like `.env.backup`, `.env.backup*`, or timestamped backups (e.g., `.env.example.backup-20260503-014323`) must never be committed. Always add backup file patterns to `.gitignore`.

- **Network exposure.** By default, services bind to all interfaces. In production, put a reverse proxy (e.g. nginx, Caddy) with TLS in front and restrict direct port access.
- **Default passwords.** Change all `changeme` defaults in your `.env` before exposing any service to a network.

## Security SOPs

When adding or modifying files that may contain secrets or credentials:

1. **Update `.gitignore` immediately** — Add patterns for any backup, temp, or secret files (e.g., `.env.backup*`, `*.backup`, `*.secret`).
2. **Only commit template files** — `.env.example` is the only env-style file that should be committed; it must contain only placeholder values.
3. **Verify before commit** — Run `git status` and ensure no backup or secret files are staged before committing.
4. **CI validation** — The CI pipeline scans `.env.example` for leaked credentials; ensure placeholder values don't resemble real secrets.
