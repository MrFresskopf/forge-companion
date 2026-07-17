# Roadmap

Forge Companion complements BrewForge instead of reproducing its recipe designer or roadmap.

## Milestone 0 — Foundation (working)

- [x] installable Python package and CLI
- [x] environment-only token handling
- [x] read-only API client
- [x] endpoint diagnostics
- [x] validated, paginated JSON collection snapshot
- [x] automated tests, linting, and type checking

## Milestone 1 — Protect and inspect

- [ ] snapshot schema validation and manifest
- [ ] optional, rate-limit-aware full export of brew details, notes, and readings
- [ ] optional compression
- [ ] inventory audit for duplicates, suspicious units, expiry dates, and old hops
- [ ] conservative Brewfather/BrewForge comparison report
- [ ] machine-readable `doctor --json` output

## Milestone 2 — Understand fermentation

- [ ] CSV export of brew readings
- [ ] attenuation and fermentation-rate calculations
- [ ] stale-data and outlier detection
- [ ] standalone HTML fermentation report
- [ ] shareable SVG/PNG Fermentation Brief
- [ ] split-batch comparison

## Milestone 3 — Connect

- [ ] RAPT/iSpindel/Tilt webhook relay
- [ ] MQTT publishing
- [ ] InfluxDB/Grafana export
- [ ] Home Assistant integration
- [ ] notifications without high-frequency BrewForge API polling

## Milestone 4 — Experimental automation

- [ ] simulation-only spunding decision engine
- [ ] stale-data and confidence gates
- [ ] one-shot, idempotent Shelly actions
- [ ] read-back verification and audit log
- [ ] explicit experimental warning and mechanical safety requirements

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
