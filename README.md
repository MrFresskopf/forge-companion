# Forge Companion

> **Unofficial community project.** Forge Companion is not affiliated with or endorsed by BrewForge. BrewForge is a product and trademark of its respective owner.

Read-only tools that help brewers protect, inspect, and understand data stored in [BrewForge](https://brewforge.sh/).

## Why this exists

BrewForge is the brewing system of record. Forge Companion provides connective tissue around it:

- **Protect:** portable snapshots of supported API collections
- **Inspect:** API and endpoint diagnostics
- **Understand:** offline inventory audits, fermentation reports, and fail-closed simulations
- **Connect:** MQTT, Home Assistant, and device bridges (future milestones)

The project deliberately starts read-only. It does not create, update, or delete BrewForge data.

## Current commands

### Find brew UUIDs

```bash
forge-companion brews
forge-companion brews --page 2 --limit 25
```

The command makes exactly one read-only `GET /brews` request and prints only each brew's sanitized
name and canonical UUID. It does not select a brew automatically or fetch details, notes, or
readings. If the API reports another page, the command prints the next `--page` value instead of
requesting it automatically.

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

### Audit inventory offline

```bash
forge-companion inventory-audit snapshots/brewforge.json
forge-companion inventory-audit snapshots/brewforge.json --as-of 2026-07-17
```

The audit reads only the local snapshot and currently reports:

- expired inventory
- negative quantities
- missing yeast or miscellaneous-item units
- conservative possible duplicates using category-specific identity fields

Findings are advisory. Possible duplicates are never merged or changed automatically.

### Create a fermentation brief

```bash
forge-companion fermentation-brief BREW_ID \
  --output reports/fermentation-BREW_ID.md \
  --temperature-unit C
```

The command pins the exact brew UUID and uses two read-only API requests: one for the brew and
one for its stored fermentation readings. It writes a local Markdown report with observation
duration, gravity change, an optional 24-hour least-squares slope, temperature range, freshness,
largest telemetry gap, and recent readings.

Temperature units are never guessed. Omit `--temperature-unit` to label values as raw API values.
The report is descriptive: it does not declare fermentation complete and cannot trigger hardware.
Reports may contain private brew names, comments, and measurements, so `reports/` is gitignored.

### Export fermentation readings as CSV

```bash
forge-companion fermentation-csv BREW_ID
forge-companion fermentation-csv BREW_ID --output reports/readings-BREW_ID.csv
```

The command pins one exact brew UUID and makes exactly one read-only request for its stored
fermentation readings. It writes accepted readings in chronological order with these stable columns:

```text
id,timestamp_utc,gravity_sg,temperature_raw,pressure,ph,comment
```

Temperature units are not guessed. The `temperature_raw` column preserves the validated numeric API
value, while missing optional measurements remain empty cells. The completion message reports how
many input records were rejected and how many timestamps contained conflicting readings, so a
partial export is never silent. If no valid reading remains, no CSV is written.

Text cells that spreadsheet applications could interpret as formulas are prefixed with an apostrophe.
CSV files can contain private comments, identifiers, and measurements; the default destination is the
gitignored `reports/` directory.

### Simulate a spunding threshold decision

```bash
forge-companion spunding-advisor BREW_ID \
  --trigger-sg 1.0120 \
  --max-age-minutes 90 \
  --max-gap-minutes 120 \
  --confirmations 2
```

The command pins one exact brew UUID and makes exactly one read-only request for its stored
fermentation readings. It prints one of three evidence-backed statuses:

- `NO_DECISION`: telemetry is malformed, conflicted, stale, insufficient, or too widely spaced
- `WAIT`: at least one of the latest confirmation readings is above the explicit trigger SG
- `CONDITION_MET`: every latest confirmation reading is at or below the explicit trigger SG

`CONDITION_MET` is a simulation result, not a device command or a declaration that actuation is
safe. Forge Companion does not contact a Shelly, verify pressure, confirm valve position, or test a
regulator or PRV. The trigger SG must be calculated separately from the actual beer volume,
fermenter volume and headspace, expected FG, temperature, desired pressure, and carbonation target.

The command has no scheduler. Calling one pinned readings endpoint hourly would already use roughly
720 of BrewForge's documented 1,000 monthly requests.

## Requirements

All commands require Python 3.11 or newer.

`inventory-audit` works entirely offline with an existing Forge Companion snapshot. It does not
need a BrewForge subscription, API access, or token.

Commands that contact BrewForge additionally require:

- A BrewForge plan with API access
- A BrewForge API token with the narrowest suitable read scopes:
  - `brews:read`
  - `inventory:read`
  - `equipment:read`
  - `styles:read`

BrewForge currently documents limits of 100 requests per hour and 1,000 requests per month. Forge Companion paginates collections and avoids one-request-per-item behavior.

## Install for development

```bash
git clone https://github.com/MrFresskopf/forge-companion.git
cd forge-companion
uv sync --extra dev
```

Set the token in the current shell. Do not put a real token in source control:

```bash
export BREWFORGE_API_TOKEN='bfk_your_token_here'
```

In Windows Command Prompt (`cmd.exe`), enter the token without placing it in the command history:

```cmd
set /p "BREWFORGE_API_TOKEN=BrewForge API token: "
```

Remove it from that Command Prompt session when finished:

```cmd
set "BREWFORGE_API_TOKEN="
```

Run a command:

```bash
uv run forge-companion brews
uv run forge-companion doctor
uv run forge-companion snapshot --output snapshots/brewforge.json
uv run forge-companion fermentation-csv BREW_ID
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
2. machine-readable audit output and additional inventory rules
3. CSV export and optional HTML fermentation charts
4. webhook/MQTT bridge
5. Home Assistant integration
6. experimental automation modules with fail-closed safeguards

See [docs/ROADMAP.md](docs/ROADMAP.md) for scope and non-goals.

## Contributing

Small, test-backed changes are welcome. Please keep the default path read-only and never include real user data or API tokens in issues, fixtures, screenshots, or commits.

## License

MIT — see [LICENSE](LICENSE).
