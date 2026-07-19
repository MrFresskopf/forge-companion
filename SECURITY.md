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

Fermentation briefs can contain brew names, comments, timestamps, and measurements. Keep them in
the gitignored `reports/` directory unless you deliberately review and share a report.

Fermentation CSV exports can contain identifiers, comments, timestamps, and measurements. They use
spreadsheet-safe text cells for formula-like IDs and comments, but remain private brewing data and
belong in the gitignored `reports/` directory unless deliberately reviewed and shared.

The spunding advisor is simulation-only. It performs one GET for a pinned brew's readings and
prints a threshold evaluation; it has no scheduler, device client, actuator state, or write path.
Its output cannot verify pressure, valve position, regulator behavior, PRV condition, or mechanical
success. Never use it as an overpressure safeguard or as a substitute for independent mechanical
protection and manual override.

## Reporting vulnerabilities

Do not publish credential leaks, authorization bypasses, or destructive API behavior in a public issue. Contact the maintainers privately with:

- affected version
- reproduction steps using redacted identifiers
- impact
- proposed mitigation, if known

BrewForge platform vulnerabilities should also be disclosed directly to BrewForge.
