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

Shelly Cloud uses a separate versioned profile containing the assigned `*.shelly.cloud` tenant,
12-hex device ID, and authorization key. `hopper cloud-auth login` collects the key through hidden,
confirmed input and stores the complete profile under a separate native-keyring account. There is no
plaintext or environment-variable fallback. Status and logout never display profile values, malformed
stored profiles fail closed, and logout can remove malformed entries without first parsing them. A
Shelly Cloud authorization key may grant access to every device in its account; use a dedicated
account or similarly narrow boundary where practical and rotate the key immediately after exposure.

The optional report preferences file never stores credentials. It currently contains only an
explicitly remembered `C` or `F` temperature-unit choice. `FORGE_COMPANION_CONFIG_DIR` can relocate
that non-secret file but does not change token resolution or credential-store behavior.

If a token is exposed, revoke it in BrewForge immediately and create a replacement with the narrowest scopes needed.

## Current access model

Version 0.3 does not create, change, or delete BrewForge data. The BrewForge HTTP client exposes only
GET requests. Shelly integration is a separate trust boundary: read-only local and Cloud status
adapters remain narrow, while the experimental `hopper fire` command can send one explicitly
confirmed Cloud pulse for a previously armed plan. Collection snapshots are local JSON files and may
contain private brewing data, so users are responsible for protecting and encrypting them. They are
not complete or directly restorable account backups.

New v3 collection snapshots include a strict manifest and canonical SHA-256 digest. `snapshot
validate` rejects ambiguous JSON, unsupported schema variants, inconsistent collection counts, and
modified content without contacting BrewForge. The digest is unkeyed: it detects changes but does not
authenticate the author or source, prevent a capable attacker from replacing both data and digest, or
encrypt private data. Older snapshot formats are rejected rather than reinterpreted.

`doctor --json` serializes only allowlisted endpoint paths, status values, HTTP status codes, and fixed
error codes under the closed `forge-companion-doctor-v2` schema. It never includes response bodies,
exception text, or credential values. The normal human doctor output likewise reduces invalid-response
failures to a generic message instead of reflecting parser details. Diagnostic results can still reveal
which documented scopes succeeded, so treat automation logs according to their operational context.

Fermentation briefs can contain brew names, comments, timestamps, and measurements. Keep them in
the gitignored `reports/` directory unless you deliberately review and share a report.

Fermentation CSV exports can contain identifiers, comments, timestamps, and measurements. They use
spreadsheet-safe text cells for formula-like IDs and comments, but remain private brewing data and
belong in the gitignored `reports/` directory unless deliberately reviewed and shared.

Standalone HTML fermentation reports contain the same private telemetry and comments. They sanitize
and HTML-escape dynamic text, cap displayed rejection reasons, embed no external dependencies, and
ship with a restrictive Content Security Policy. These defenses do not make a report public-safe;
review it deliberately before moving it out of the gitignored `reports/` directory.

Interactive HTML selection makes one bounded brew-list GET per explicitly displayed page and one
readings GET after the user chooses a displayed number. It never selects the newest or active brew
automatically, never fetches
brew details, and follows another list page only after explicit `n`, `p`, or `--page` input. The
automatic `report` selection path requires an interactive terminal; scripts and pipelines must pin a
UUID.

The spunding advisor is simulation-only. It performs one GET for a pinned brew's readings and
prints a threshold evaluation; it has no scheduler, device client, actuator state, or write path.
Its output cannot verify pressure, valve position, regulator behavior, PRV condition, or mechanical
success. Never use it as an overpressure safeguard or as a substitute for independent mechanical
protection and manual override.

Remote-hopper plans support two distinct modes. Simulation plans remain offline. A Cloud one-shot plan
stores only the normalized tenant and device ID, never the authorization key, and accepts at most a
one-second pulse. `hopper check` is a separate read-only rehearsal: it validates an armed, reached Cloud
plan, verifies exact native-profile target binding, sends one status request, requires online electrical
`OFF`, never constructs an actuator, and never changes the plan. Its result is temporary telemetry and
neither authorizes a pulse nor replaces the fresh preflight in `hopper fire`.

`hopper fire` requires an armed plan past its trigger, a current versioned operator attestation that ten
successful full-assembly tests were performed, and a native credential profile matching the plan. The
attestation is stored as non-secret local preference state and is not mechanical, sensor-based, or
independent verification. The command checks that gate before requiring exact interactive `FIRE`
confirmation on an attached terminal; piped input is rejected. Only after confirmation does a fresh
read-only preflight verify that
the device is online and electrically OFF. It then observes the Cloud API rate boundary and persists
`FIRE_REQUESTED` atomically before the one switch attempt. The file is flushed before replacement;
Windows uses a write-through move and POSIX flushes the containing directory before the command
proceeds. There is no scheduler or automatic retry.

The actuator is a separate type from both read-only clients. It exposes one fixed channel pulse using
Cloud v2 `/v2/devices/api/set/switch`, `on: true`, and `toggle_after`; it has no generic request or raw relay
method. After waiting for the device timer and at least the provider's one-second request interval, it
reads status once. Only online electrical `OFF` completes the plan as `LOCKED`. Any ambiguous outcome
leaves the plan consumed at `FIRE_REQUESTED`, preventing an ordinary second attempt. A crash can leave
the exclusive sidecar lock in place; remove only the stale lock after confirming no process remains,
never modify the plan to make it fireable again.

These controls do not provide provider-side idempotency or mechanical feedback. A timeout may mean the
pulse executed even though its response was lost. Electrical `OFF` cannot prove winch motion, cable
travel, magnet release, or hop addition. The complete installed mechanism needs repeated under-load
qualification, a conservative measured pulse, device-side auto-off, mechanical protection, and manual
isolation. The software gate records only the operator's one-time declaration; it does not observe the
tests or prove their outcome. One `FIRE` confirmation authorizes exactly one attempt.
`hopper shelly-status` is a separate read-only local-network check. Its client exposes only
`GET /rpc/Switch.GetStatus`, rejects redirects, ambiguous base URLs, malformed or duplicate-key JSON,
and responses larger than 64 KiB. Its internally owned HTTP client ignores environment proxy settings
and is closed deterministically after each CLI invocation. It does not resolve BrewForge credentials
and has no generic RPC, relay-write,
plan-transition, or scheduler interface. The current implementation has no Shelly authentication
support and should be used only on a trusted local network; enabling device authentication will make
it fail closed. Never place credentials in `--device-url`—credential-bearing URLs are rejected and
command arguments may remain in shell history.

`hopper cloud-status` is a separate read-only Internet check through Shelly Cloud Control API v2.
It is intended for a remote brewery behind NAT and requires no inbound port, public device RPC, or
VPN. The adapter accepts only a canonical subdomain below `shelly.cloud`, one 12-hex device ID, and
one channel. It sends exactly one HTTPS request to `/v2/devices/api/get`, selects only that switch's
status, disables redirects and environment proxies, uses a five-second timeout, and streams at most
64 KiB. Device IDs and response fields must match the request exactly; duplicate-key, non-finite,
oversized, malformed, or mismatched responses fail closed. An offline response produces `UNKNOWN`
rather than trusting stale relay telemetry.

The Cloud v2 status endpoint is POST-based even though it is observational. The read-only adapter
exposes no arbitrary endpoint, `Switch.Set`, actuator, scheduling, retry, or hopper transition. The
live one-shot actuator is a separate type and command with the controls described above. The authorization key is necessarily sent to
the assigned Shelly Cloud host as required by the provider API, so errors and terminal output are
sanitized and redirects are forbidden. The Cloud Control API is provider-managed and may change;
invalid or incompatible responses fail closed. Never port-forward a Shelly RPC endpoint as a
substitute for this path.

Shelly status is electrical telemetry, not mechanical feedback. `OFF` cannot prove that a winch was
physically isolated, that a hopper moved, or that an endpoint was reached. A separately measured hard
timeout, manual isolation, and direct operator observation remain prerequisites for the current
supervised live hopper action. Independent sensing remains an optional future hardening measure.

## Reporting vulnerabilities

Use GitHub's [private vulnerability reporting form](https://github.com/MrFresskopf/forge-companion/security/advisories/new).
Do not publish credential leaks, authorization bypasses, or destructive API behavior in a public
issue. Include:

- affected version
- reproduction steps using redacted identifiers
- impact
- proposed mitigation, if known

BrewForge platform vulnerabilities should also be disclosed directly to BrewForge.
