# Security Policy

## Credentials

Forge Companion reads the BrewForge API token from `BREWFORGE_API_TOKEN`. Never place a real token in:

- source files or committed `.env` files
- command examples
- screenshots or logs
- test fixtures
- bug reports
- generated public demo data

If a token is exposed, revoke it in BrewForge immediately and create a replacement with the narrowest scopes needed.

## Current access model

Version 0.1 is intentionally read-only. The HTTP client exposes only GET requests. Collection
snapshots are local JSON files and may contain private brewing data, so users are responsible for
protecting and encrypting them. They are not complete or directly restorable account backups.

## Reporting vulnerabilities

Do not publish credential leaks, authorization bypasses, or destructive API behavior in a public issue. Contact the maintainers privately with:

- affected version
- reproduction steps using redacted identifiers
- impact
- proposed mitigation, if known

BrewForge platform vulnerabilities should also be disclosed directly to BrewForge.
