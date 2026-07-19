# Roadmap

Forge Companion complements BrewForge instead of reproducing its recipe designer or roadmap.

Milestones describe capability tracks rather than a strict delivery order. Read-only analysis and
safety work may advance ahead of broader exports or integrations when that creates practical value.

## Milestone 0 — Foundation (working)

- [x] installable Python package and CLI
- [x] environment-only token handling
- [x] read-only API client
- [x] endpoint diagnostics
- [x] one-page, read-only brew listing with sanitized names and canonical UUIDs
- [x] validated, paginated JSON collection snapshot
- [x] automated tests, linting, and type checking

## Milestone 1 — Protect and inspect

- [ ] snapshot schema validation and manifest
- [ ] optional, rate-limit-aware full export of brew details, notes, and readings
- [ ] optional compression
- [x] offline inventory audit for expiry, negative quantities, missing units, and conservative duplicates
- [ ] machine-readable inventory audit output and additional plausibility rules
- [x] read-only Markdown fermentation brief with data-quality metrics
- [x] standalone HTML fermentation charts
- [ ] conservative Brewfather/BrewForge comparison report
- [ ] machine-readable `doctor --json` output

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
- [ ] read-only Shelly connectivity and state check
- [ ] device-independent dry-run actuation plan
- [ ] one-shot, explicitly armed Shelly action with timeout and idempotency
- [ ] read-back verification and audit log
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
