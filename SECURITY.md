# Security Policy

## Credentials

Forge Companion reads the BrewForge API token from a supported native OS credential store. An
explicit, valid `BREWFORGE_API_TOKEN` environment variable takes precedence for CI, scripts, and
temporary sessions. Whitespace-only values are ignored; values containing whitespace are rejected and
block stored credential use until corrected or unset. `auth login` uses hidden confirmed input and
never puts the token in a command argument; `auth status` and `auth logout` never print it. Never place
a real token in:

- source files or committed `.env` files
- command examples
- screenshots or logs
- test fixtures
- bug reports
- generated public demo data

Native credential storage is restricted to Windows Credential Manager, macOS Keychain, and Linux
Secret Service backends. Forge Companion fails closed when no supported native backend is available;
it does not create a plaintext keyring or `.env` fallback. `auth login`, `status`, and `logout` are
offline. `auth logout` deletes only the stored entry and deliberately leaves environment variables
unchanged.

If a token is exposed, revoke it in BrewForge immediately and create a replacement with the narrowest scopes needed.

## Current access model

Version 0.1 is intentionally read-only. The HTTP client exposes only GET requests. Collection
snapshots are local JSON files and may contain private brewing data, so users are responsible for
protecting and encrypting them. They are not complete or directly restorable account backups.

New v2 collection snapshots include a strict manifest and canonical SHA-256 digest. `snapshot
validate` rejects ambiguous JSON, unsupported schema variants, inconsistent collection counts, and
modified content without contacting BrewForge. The digest is unkeyed: it detects changes but does not
authenticate the author or source, prevent a capable attacker from replacing both data and digest, or
encrypt private data. Inventory audit validates v2 before analysis; legacy v1 files remain readable but
do not have an embedded integrity proof.

Fermentation briefs can contain brew names, comments, timestamps, and measurements. Keep them in
the gitignored `reports/` directory unless you deliberately review and share a report.

Fermentation CSV exports can contain identifiers, comments, timestamps, and measurements. They use
spreadsheet-safe text cells for formula-like IDs and comments, but remain private brewing data and
belong in the gitignored `reports/` directory unless deliberately reviewed and shared.

Standalone HTML fermentation reports contain the same private telemetry and comments. They sanitize
and HTML-escape dynamic text, cap displayed rejection reasons, embed no external dependencies, and
ship with a restrictive Content Security Policy. These defenses do not make a report public-safe;
review it deliberately before moving it out of the gitignored `reports/` directory.

Interactive HTML selection makes one bounded brew-list GET and one readings GET after the user
chooses a displayed number. It never selects the newest or active brew automatically, never fetches
brew details, and never follows additional list pages without an explicit `--page` value.

The spunding advisor is simulation-only. It performs one GET for a pinned brew's readings and
prints a threshold evaluation; it has no scheduler, device client, actuator state, or write path.
Its output cannot verify pressure, valve position, regulator behavior, PRV condition, or mechanical
success. Never use it as an overpressure safeguard or as a substitute for independent mechanical
protection and manual override.

## Reporting vulnerabilities

Use GitHub's [private vulnerability reporting form](https://github.com/MrFresskopf/forge-companion/security/advisories/new).
Do not publish credential leaks, authorization bypasses, or destructive API behavior in a public
issue. Include:

- affected version
- reproduction steps using redacted identifiers
- impact
- proposed mitigation, if known

BrewForge platform vulnerabilities should also be disclosed directly to BrewForge.
