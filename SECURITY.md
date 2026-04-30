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
- **Docker socket access.** `open-webui` mounts `/var/run/docker.sock`. Restrict access to this stack to trusted users only.
- **Network exposure.** By default, services bind to all interfaces. In production, put a reverse proxy (e.g. nginx, Caddy) with TLS in front and restrict direct port access.
- **Default passwords.** Change all `changeme` defaults in your `.env` before exposing any service to a network.
