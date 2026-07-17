# Forge Companion

> **Unofficial community project.** Forge Companion is not affiliated with or endorsed by BrewForge. BrewForge is a product and trademark of its respective owner.

Read-only tools that help brewers protect, inspect, and understand data stored in [BrewForge](https://brewforge.sh/).

## Why this exists

BrewForge is the brewing system of record. Forge Companion provides connective tissue around it:

- **Protect:** portable snapshots of supported API collections
- **Inspect:** API and endpoint diagnostics
- **Understand:** fermentation reports and inventory audits (next milestones)
- **Connect:** MQTT, Home Assistant, and device bridges (future milestones)

The project deliberately starts read-only. It does not create, update, or delete BrewForge data.

## Current commands

### Diagnose API access

```bash
forge-companion doctor
```

The command checks the documented collections for brews, inventory, equipment, and style profiles. It uses seven API requests and reports endpoint failures individually.

### Create a JSON collection snapshot

```bash
forge-companion snapshot
forge-companion snapshot --output snapshots/my-brewforge-collections.json
```

The snapshot contains every validated page returned by the supported top-level collection
endpoints. API credentials are not included.

> This is **not yet a complete or restorable account backup**. Version 0.1 does not fetch
> per-brew details, notes, fermentation readings, or data unavailable through the documented
> API. Calling it a collection snapshot makes that scope explicit and avoids spending one API
> request per brew and subresource without the user's informed choice.

## Requirements

- Python 3.11 or newer
- A BrewForge plan with API access
- A BrewForge API token with the narrowest suitable read scopes:
  - `brews:read`
  - `inventory:read`
  - `equipment:read`
  - `styles:read`

BrewForge currently documents limits of 100 requests per hour and 1,000 requests per month. Forge Companion paginates collections and avoids one-request-per-item behavior.

## Install for development

```bash
git clone <repository-url>
cd forge-companion
uv sync --extra dev
```

Set the token in the current shell. Do not put a real token in source control:

```bash
export BREWFORGE_API_TOKEN='bfk_your_token_here'
```

Run a command:

```bash
uv run forge-companion doctor
uv run forge-companion snapshot --output snapshots/brewforge.json
```

## Quality checks

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

## Safety model

- The initial API client exposes only `GET`.
- Tokens come exclusively from `BREWFORGE_API_TOKEN`.
- `.env` files and generated snapshots are ignored by Git.
- Snapshots are written atomically through a uniquely named temporary file.
- Pagination must make progress and match the API's declared total; collection processing is
  capped at 100 pages.
- HTTP, response-validation, and file errors stop snapshots with a concise CLI error instead of
  creating a misleading partial success.
- Future write support, if any, requires a separate opt-in design with dry runs and read-back verification.

See [SECURITY.md](SECURITY.md) for responsible disclosure and credential handling.

## Roadmap

The next planned vertical slices are:

1. snapshot manifest and validation
2. inventory audit with human-readable findings
3. CSV export and shareable fermentation brief
4. webhook/MQTT bridge
5. Home Assistant integration
6. experimental automation modules with fail-closed safeguards

See [docs/ROADMAP.md](docs/ROADMAP.md) for scope and non-goals.

## Contributing

Small, test-backed changes are welcome. Please keep the default path read-only and never include real user data or API tokens in issues, fixtures, screenshots, or commits.

## License

MIT — see [LICENSE](LICENSE).
