# Changelog

All notable changes to Forge Companion are documented in this file.

The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Native OS credential storage through `auth login`, `auth status`, and `auth logout`, with an explicit `BREWFORGE_API_TOKEN` override and no plaintext fallback.
- Versioned snapshot-v2 manifests with generator metadata, collection counts, explicit scope exclusions, and a canonical SHA-256 integrity digest.
- Offline `snapshot validate FILE` checks for strict JSON, exact schema, count consistency, supported collections, and content integrity.
- A comfort-oriented `report` command with automatic numbered brew selection, explicit next/previous-page navigation, and an optional remembered C/F preference.
- A short successful start page when `forge-companion` is run without arguments.
- An `inventory` command that reuses the standard snapshot path by default.

### Changed

- Inventory audits validate v2 snapshot schema and integrity before analysis while retaining strict read support for legacy v1 snapshots.
- The root help now emphasizes everyday commands while keeping legacy format-specific commands available for compatible scripts.
- Snapshot validation and inventory checks reuse `snapshots/brewforge-collections.json` when no path is supplied.
- HTML, Markdown, and CSV exports share one atomic text writer without weakening temporary-file cleanup or replacement semantics.

## [0.1.1] — 2026-07-19

### Fixed

- Prevent raw authenticated HTTP transport errors from reaching terminal output in `doctor`, `snapshot`, `fermentation-brief`, and `spunding-advisor`.

## [0.1.0] — 2026-07-19

First public developer-preview release.

### Added

- Read-only BrewForge API client with environment-only token handling.
- API diagnostics through `doctor` with stable, concise failure behavior.
- Validated collection snapshots with defensive pagination and atomic writes.
- Offline inventory audits for expiry dates, negative quantities, missing units, and conservative duplicate detection.
- Sanitized brew-name and UUID listing.
- Markdown fermentation briefs with telemetry quality checks.
- Spreadsheet-safe CSV exports with deterministic conflict handling.
- Standalone offline HTML fermentation reports with inline SVG charts.
- Interactive, paginated brew selection for HTML reports.
- Simulation-only spunding advisor with stale-data, cadence, confirmation, and timestamp-conflict gates.
- Windows, Python 3.11, and Python 3.13 CI coverage.

### Safety and scope

- The API client exposes only HTTP `GET` operations.
- Forge Companion does not create, update, or delete BrewForge data.
- The spunding advisor does not contact or control hardware.
- Collection snapshots are not described as complete or restorable account backups.

[Unreleased]: https://github.com/MrFresskopf/forge-companion/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/MrFresskopf/forge-companion/releases/tag/v0.1.1
[0.1.0]: https://github.com/MrFresskopf/forge-companion/releases/tag/v0.1.0
