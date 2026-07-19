# Command guide

Forge Companion uses the `BREWFORGE_API_TOKEN` environment variable for every command that contacts
BrewForge. The inventory audit is the exception: it only reads a local snapshot.

BrewForge currently documents limits of 100 requests per hour and 1,000 requests per month. Forge
Companion keeps network use explicit and avoids hidden one-request-per-item behavior.

## `doctor`

Check authentication and every documented read-only collection used by Forge Companion:

```bash
forge-companion doctor
```

The command checks brews, inventory, equipment, and style profiles with seven API requests. Each
endpoint reports `OK` or `FAIL` independently.

## `brews`

List human-readable brew names and canonical UUIDs:

```bash
forge-companion brews
forge-companion brews --page 2 --limit 25
```

The command makes exactly one `GET /brews` request. It does not fetch brew details, notes, or
readings. If another page exists, it prints the next `--page` value instead of requesting it
silently.

## `snapshot`

Save validated top-level API collections as JSON:

```bash
forge-companion snapshot
forge-companion snapshot --output snapshots/my-brewforge-collections.json
```

Credentials are never written to the file. Writes are atomic, and validation or network errors stop
the operation instead of leaving a misleading partial snapshot.

> [!WARNING]
> This is not yet a complete or restorable account backup. Version 0.1 does not fetch per-brew
> details, notes, fermentation readings, or data unavailable through the documented API.

## `inventory-audit`

Audit a local Forge Companion snapshot without contacting BrewForge:

```bash
forge-companion inventory-audit snapshots/brewforge-collections.json
forge-companion inventory-audit snapshots/brewforge-collections.json --as-of 2026-07-17
```

Current checks cover expired inventory, negative quantities, missing yeast or miscellaneous-item
units, and conservative possible duplicates. Findings are advisory; Forge Companion never merges or
changes inventory.

## `fermentation-brief`

Create a local Markdown report for one exact brew:

```bash
forge-companion fermentation-brief BREW_ID \
  --output reports/fermentation-BREW_ID.md \
  --temperature-unit C
```

This command uses two requests: one for the brew and one for its readings. The report includes the
observation period, gravity change, an optional 24-hour least-squares slope, temperature range,
reading freshness, largest telemetry gap, and recent readings.

Temperature units are never guessed. Omit `--temperature-unit` to label values as raw API values.

## `fermentation-csv`

Export accepted readings in chronological order:

```bash
forge-companion fermentation-csv BREW_ID
forge-companion fermentation-csv BREW_ID --output reports/readings-BREW_ID.csv
```

The stable columns are:

```text
id,timestamp_utc,gravity_sg,temperature_raw,pressure,ph,comment
```

Missing optional measurements remain empty. Text that spreadsheet applications could interpret as a
formula is prefixed with an apostrophe. The completion message reports rejected records and
conflicting timestamps; if no valid reading remains, no CSV is written.

## `fermentation-html`

Create a self-contained visual fermentation report:

```bash
forge-companion fermentation-html BREW_ID \
  --title "Lithuanian Session Witbier" \
  --temperature-unit C \
  --output reports/lithuanian-session-witbier.html
```

The command uses one readings request and does not fetch brew details. Pass `--title` for a friendly
report name. The output includes summary metrics, data-quality evidence, recent readings, and an
inline SVG gravity/temperature chart.

The HTML has no JavaScript, CDN, remote fonts, images, or tracking. Dynamic content is sanitized and
escaped, a restrictive Content Security Policy blocks external content, and writes are atomic.

## `spunding-advisor`

Simulate a fail-closed gravity threshold decision:

```bash
forge-companion spunding-advisor BREW_ID \
  --trigger-sg 1.0120 \
  --max-age-minutes 90 \
  --max-gap-minutes 120 \
  --confirmations 2
```

It returns one of three statuses:

- `NO_DECISION`: telemetry is malformed, conflicted, stale, insufficient, or too widely spaced
- `WAIT`: at least one confirmation reading is above the threshold
- `CONDITION_MET`: all required confirmation readings are at or below the threshold

`CONDITION_MET` is a simulation result, not a device command or a declaration that actuation is safe.
The command does not contact a Shelly, verify pressure, confirm valve position, or test a regulator or
PRV. Calculate the SG threshold separately from actual beer volume, headspace, expected FG,
temperature, desired pressure, and carbonation target.

There is no scheduler. Polling one readings endpoint hourly would use roughly 720 of BrewForge's
documented 1,000 monthly requests.

## API scopes

Use the narrowest scopes needed for your task:

- `brews:read`
- `inventory:read`
- `equipment:read`
- `styles:read`

Reports may contain private brew names, comments, identifiers, and measurements. Keep them in the
gitignored `reports/` directory unless you have reviewed them for sharing.
