# Changelog

All notable changes to Forge Companion are documented in this file.

The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Interactive brew selection now recovers from invalid choices without refetching the current page.
- Bare `auth`, `hopper`, and `hopper cloud-auth` groups now show their available commands without
  performing an action, while snapshot and first-run guidance explain the intended next step.

### Fixed

- CI actions now run natively on Node.js 24 while retaining explicit cache-pruning behavior.
- `report` now validates explicit brew UUIDs before credential or API access and points
  non-interactive users to `forge-companion brews` for UUID discovery.
- Inventory audits now validate `--as-of` before reading a snapshot and explain how to create a
  missing standard snapshot.
- Linux CI type-checking now resolves Windows-only `ctypes` symbols lazily without weakening the
  durable Windows `MoveFileExW` replacement path.
- Mypy now checks `src` directly so an older installed wheel cannot mask working-tree changes.

## [0.2.0] — 2026-07-26

### Added

- Native OS credential storage through `auth login`, `auth status`, and `auth logout`, with an explicit `BREWFORGE_API_TOKEN` override and no plaintext fallback.
- Versioned snapshot-v2 manifests with generator metadata, collection counts, explicit scope exclusions, and a canonical SHA-256 integrity digest.
- Offline `snapshot validate FILE` checks for strict JSON, exact schema, count consistency, supported collections, and content integrity.
- A comfort-oriented `report` command with automatic numbered brew selection, explicit next/previous-page navigation, and an optional remembered C/F preference.
- A short successful start page when `forge-companion` is run without arguments.
- An `inventory` command that reuses the standard snapshot path by default.
- Offline `hopper plan`, `hopper arm`, `hopper simulate`, and `hopper status` commands with strict
  local state history, exclusive transition locks, non-overwriting creation, atomic files, SHA-256
  change detection, explicit one-shot locking, and no network or hardware path.
- A narrow `hopper shelly-status` command for strict read-only local switch-state checks, with no
  generic RPC method, relay-write path, BrewForge credential lookup, or mechanical-success claim.
- A separate read-only Shelly Cloud status adapter (`hopper cloud-status`) using the provider's
  documented Cloud Control API v2. No inbound port, public device RPC, or VPN required.
- A separate native-keyring Shelly Cloud profile through `hopper cloud-auth login`, `status`, and
  `logout` with hidden confirmed input, strict schema validation, and no plaintext fallback.
- An experimental `hopper plan --cloud` and explicitly confirmed `hopper fire` one-shot with a fresh
  online-OFF preflight after confirmation. It records `FIRE_REQUESTED` before one non-retried Cloud
  pulse, uses device-side `toggle_after`, verifies an online electrical OFF read-back, and leaves
  ambiguous outcomes consumed for manual investigation.
- A read-only `hopper check PLAN` rehearsal that validates the armed plan, reached trigger, exact
  credential target, and one live online/OFF status response without constructing an actuator or
  changing the plan.
- A supervised full-assembly hopper qualification protocol and reusable ten-trial CSV template.

### Changed

- Inventory audits validate v2 snapshot schema and integrity before analysis while retaining strict read support for legacy v1 snapshots.
- The root help now emphasizes everyday commands while keeping legacy format-specific commands available for compatible scripts.
- Snapshot validation and inventory checks reuse `snapshots/brewforge-collections.json` when no path is supplied.
- HTML, Markdown, and CSV exports share one atomic text writer without weakening temporary-file cleanup or replacement semantics.
- Read-only Shelly status requests now reject responses above 64 KiB, ignore environment proxy
  settings for internally created clients, and close owned HTTP resources deterministically.
- Product messaging now distinguishes read-only BrewForge access and diagnostics from the isolated,
  guarded experimental Shelly actuator.
- The Windows-only `pywin32` dependency now carries an explicit platform marker.
- Pytest now imports the working tree from `src`, preventing an older installed wheel from masking
  uncommitted source changes during local verification.

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

[Unreleased]: https://github.com/MrFresskopf/forge-companion/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MrFresskopf/forge-companion/releases/tag/v0.2.0
[0.1.1]: https://github.com/MrFresskopf/forge-companion/releases/tag/v0.1.1
[0.1.0]: https://github.com/MrFresskopf/forge-companion/releases/tag/v0.1.0
