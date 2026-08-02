# Roadmap

Forge Companion focuses on safe Shelly control for brewery automation. BrewForge integration remains a
supporting read-only source for brew context, telemetry, reports, and snapshots.

Milestones describe capability tracks rather than a strict delivery order. Read-only analysis and
safety work may advance ahead of broader exports or integrations when that creates practical value.

## Path to 1.0

Forge Companion 1.0 means a stable, documented safety core for offline Shelly planning, read-only
device diagnostics, and fail-closed command boundaries. The guarded Shelly Cloud one-shot may remain
experimental and outside the 1.0 compatibility promise until independent mechanical feedback and
repeated installed-system qualification exist. Read-only BrewForge tools support this core.

### Stable 1.0 scope

- offline hopper planning and simulation plus narrow read-only Shelly diagnostics
- guarded plan state transitions, explicit arming, durable one-shot consumption, and privacy-safe errors
- native Shelly Cloud credential storage with no plaintext fallback
- documented CLI names, options, exit codes, machine-readable schemas, and file-format compatibility
- supporting BrewForge read-only diagnostics, explicit brew selection, fermentation reports, and
  non-restorable snapshots

Human-readable wording may continue to improve after 1.0. Documented JSON schemas, file formats,
exit-code meanings, and non-experimental command behavior require compatibility or an announced
deprecation and migration path.

### 1.0 release gates

- [ ] publish a compatibility and deprecation policy for CLI commands, exit codes, JSON output,
  configuration, and persisted file formats
- [ ] freeze the supported snapshot schema and document how older snapshot versions are read or
  migrated
- [ ] complete repeated full-assembly qualification and evaluate independent release sensing
- [ ] keep live actuation experimental until its mechanical evidence and failure boundaries pass review
- [ ] retain stable machine-readable output for doctor diagnostics
- [ ] verify isolated installation and native-keyring boundaries on Windows, Linux, and macOS, including
  the minimum supported Python version
- [ ] maintain sanitized BrewForge contract tests for pagination, missing or additional fields, rate
  limits, timeouts, and incompatible responses
- [ ] document a stable end-user installation path from a version tag or package index instead of
  treating `@main` as the primary installation target
- [ ] publish the 0.x-to-1.0 upgrade guide, support window, security scope, and experimental-feature
  boundary
- [ ] review the CLI composition root and complete any intended command-module split before the public
  interface freeze; this is a maintainability improvement, not a feature requirement
- [ ] publish and exercise `1.0.0rc1` before the final release, including an independent security and
  release-artifact review

### Planned stabilization sequence

#### 0.3 — Shelly safety and qualification

- run supervised winch bench characterization and record measured travel, current, and timing
- repeat full-assembly release trials once the Fermzilla is available
- evaluate practical mechanical release sensing without inferring success from relay state
- preserve explicit confirmation, no-retry, device auto-off, and durable consumed-state guarantees

#### 0.4 — Public contracts and platform hardening

- define stable human-versus-machine output boundaries and exit codes
- [x] add versioned `doctor --json`
- define snapshot compatibility, migration, and deprecation rules
- modularize CLI command registration where that reduces pre-1.0 maintenance risk
- add BrewForge response-contract and failure-mode coverage
- add macOS CI and installed-package/keyring smoke tests across supported platforms
- document the stable installation and upgrade path

#### 1.0.0rc1 — Stability candidate

- stop adding broad features and accept only compatibility, documentation, security, and correctness
  fixes
- run real read-only BrewForge and three-platform installation smokes with sanitized evidence
- verify wheel, source distribution, checksums, tag provenance, and rendered public documentation
- keep live Shelly actuation explicitly experimental unless mechanical evidence is independently reviewed

#### 1.0.0 — Stable core

- release only after the candidate contract and artifacts pass without unresolved blockers
- carry forward the read-only BrewForge boundary and the separately documented experimental actuator
  boundary

### Explicitly not required for 1.0

- complete BrewForge account export or Brewfather comparison reports
- attenuation, fermentation-rate, and configurable outlier analytics
- shareable PNG/SVG fermentation briefs
- split-batch comparison while BrewForge's own implementation remains under evaluation
- webhook relays, MQTT, InfluxDB/Grafana, Home Assistant, and notifications
- authenticated local Shelly access or unattended scheduling
- BrewForge restore or any BrewForge write operation
- promoting `hopper fire` from experimental status without mechanical success sensing

## Milestone 0 — Foundation (working)

- [x] installable Python package and CLI
- [x] native OS credential storage with explicit environment override
- [x] read-only API client
- [x] endpoint diagnostics
- [x] one-page, read-only brew listing with sanitized names and canonical UUIDs
- [x] shared explicit, paginated brew selection for reports, exports, and safety simulations
- [x] comfort-oriented `report` workflow with remembered non-secret temperature-unit preference
- [x] validated, paginated JSON collection snapshot
- [x] automated tests, linting, and type checking

## Milestone 1 — Supporting read-only BrewForge tools

- [x] snapshot v3 manifest, strict offline schema validation, collection counts, scope declaration, and SHA-256 integrity check
- [ ] optional, rate-limit-aware full export of brew details, notes, and readings
- [ ] optional compression
- [x] read-only Markdown fermentation brief with data-quality metrics
- [x] standalone HTML fermentation charts
- [ ] conservative Brewfather/BrewForge comparison report
- [x] machine-readable `doctor --json` output

## Milestone 2 — Understand fermentation

- [x] deterministic, spreadsheet-safe CSV export of validated brew readings
- [ ] attenuation and fermentation-rate calculations
- [x] stale-reading, telemetry-gap, and timestamp-conflict detection
- [ ] configurable gravity and temperature outlier detection
- [x] standalone HTML fermentation report
- [ ] shareable SVG/PNG Fermentation Brief
- [ ] split-batch comparison — deferred until BrewForge's roadmap implementation can be evaluated

## Milestone 3 — Connect

- [ ] RAPT/iSpindel/Tilt webhook relay
- [ ] MQTT publishing
- [ ] InfluxDB/Grafana export
- [ ] Home Assistant integration
- [ ] notifications without high-frequency BrewForge API polling

## Milestone 4 — Experimental automation

- [x] simulation-only spunding threshold advisor
- [x] stale-data, timestamp-conflict, confirmation, and cadence gates
- [x] read-only Shelly connectivity and strict local switch-state check
- [x] read-only Shelly Cloud status check (no inbound port, VPN, or public RPC required)
- [x] device-independent offline remote-hopper plan, explicit arming, lifecycle simulation, and lock
- [x] one-shot, explicitly armed Shelly Cloud action with device timeout and durable pre-request consumption
- [x] read-only armed-plan readiness check with exact credential binding and one live OFF preflight
- [x] electrical OFF read-back verification and local state-history audit trail
- [x] supervised full-assembly qualification protocol and reusable ten-trial CSV template
- [x] persistent operator-attestation gate for ten declared successful full-assembly tests
- [ ] complete the repeated full-assembly qualification and optionally evaluate release sensing
- [x] explicit experimental warning and mechanical safety requirements

## Non-goals

- replacing BrewForge's recipe designer
- building a competing public recipe library
- scraping or redistributing private BrewForge data
- hiding API usage or bypassing subscription limits
- controlling pressure equipment without independent mechanical safeguards

## Collaboration principles

- Clearly label the project unofficial unless BrewForge grants another status.
- Use documented APIs and narrow scopes.
- Report reproducible API defects privately before public escalation.
- Coordinate features that overlap BrewForge's active roadmap.
- Prefer adapters, exports, and experiments that help validate community demand.
