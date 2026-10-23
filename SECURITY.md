# Security Policy

## Supported Versions
Only the latest major version (v1.x) receives security updates.

## Reporting a Vulnerability
Please email `security@openforge.ai` to report security vulnerabilities rather than opening public issues. We aim to respond within 48 hours.

**Warning on Sandboxing:**
OpenForge executes untrusted code from GitHub repositories (such as `pytest` or `npm test`). We strongly recommend running `openforge code` inside a Docker container or restricted VM. Do not run it on a host machine containing sensitive data.
