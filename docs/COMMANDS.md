# Command guide

Forge Companion uses a supported native OS credential store by default. `BREWFORGE_API_TOKEN` remains
an explicit override for CI, scripts, and temporary sessions. The inventory audit is the exception:
it only reads a local snapshot and needs no credential.

BrewForge currently documents limits of 100 requests per hour and 1,000 requests per month. Forge
Companion keeps network use explicit and avoids hidden one-request-per-item behavior.

Run `forge-companion` without arguments for the shortest start page. The primary everyday commands
are `report`, `snapshot`, and `inventory`; older format-specific commands remain available for scripts.

## `auth`

Manage authentication without displaying a token:

```bash
forge-companion auth login
forge-companion auth status
forge-companion auth logout
```

`login` asks twice with hidden input and writes only to the native Windows Credential Manager, macOS
Keychain, or Linux Secret Service. It does not contact BrewForge; run `doctor` afterward to validate
the credential and its read scopes. Forge Companion rejects missing or non-native keyring backends
instead of creating a plaintext fallback.

`status` reports only `environment`, `native OS credential store`, or `not configured`. It never
prints the token. A valid `BREWFORGE_API_TOKEN` takes precedence over the stored credential.
Whitespace-only values are treated as absent. Values containing whitespace are invalid and block the
stored credential until corrected or unset. `logout` deletes only the native stored entry and reports
whether a valid environment override remains active or an invalid value still blocks authentication.
All three commands are offline.

## `report`

Create the standard self-contained HTML fermentation report:

```bash
forge-companion report --temperature-unit C --remember
forge-companion report BREW_ID --output reports/pinned-brew.html
```

Without a UUID in an interactive terminal, `report` requests 25 sanitized brew names and waits for
an explicit selection. Enter a number to select, `n` to request the next page, `p` for the previous
page, or `q` to cancel. Page
changes never happen without that explicit input. Non-interactive scripts and pipelines must pass an
exact UUID and never receive an automatic prompt. The selected brew name becomes the report title.
The normal selection uses one brew-list GET plus one readings GET; every explicit `n` or `p` adds
exactly one further brew-list GET.

`--remember` requires an explicit `--temperature-unit C` or `F`. It stores only that non-secret
preference in the platform's user configuration directory. A CLI option overrides the saved value;
the API token remains exclusively in the credential store or environment override.

The UUID form is deterministic, uses one readings request, and does not fetch brew details. Pass
`--title` when a script needs a friendly report title.

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
forge-companion snapshot validate
forge-companion snapshot validate snapshots/my-brewforge-collections.json
```

Credentials are never written to the file. Writes are atomic, and validation or network errors stop
the operation instead of leaving a misleading partial snapshot. New snapshots use the v2 format and
contain the creation time, Forge Companion version, all seven supported collection names and counts,
explicit exclusions, and a SHA-256 digest over canonical UTF-8 JSON excluding only the digest field.

`snapshot validate [FILE]` is offline and defaults to `snapshots/brewforge-collections.json`. It
strictly rejects duplicate JSON keys, non-JSON numeric values,
unknown fields, unsupported formats, inconsistent counts, missing collections, malformed records,
and checksum changes. Successful output contains only manifest metadata and counts, never collection
records or the input path. The inventory audit applies the same validation to v2 files while retaining
read support for strict legacy v1 snapshots.

The SHA-256 digest detects changes; it is not a digital signature, proof that BrewForge produced the
data, access control, or encryption. Keep snapshots private and protect them like any other account
export.

> [!WARNING]
> This is not yet a complete or restorable account backup. Version 0.1 does not fetch per-brew
> details, notes, fermentation readings, or data unavailable through the documented API.

## `inventory`

Audit a local Forge Companion snapshot without contacting BrewForge:

```bash
forge-companion inventory
forge-companion inventory snapshots/my-brewforge-collections.json --as-of 2026-07-17
```

Without a path it reuses `snapshots/brewforge-collections.json`, the output of the default `snapshot`
command. The previous `inventory-audit` spelling remains available for compatible scripts.

Current checks cover expired inventory, negative quantities, missing yeast or miscellaneous-item
units, and conservative possible duplicates. Findings are advisory; Forge Companion never merges or
changes inventory. v2 input must pass schema and SHA-256 validation before any finding is calculated;
legacy v1 snapshots remain accepted but have no embedded integrity proof.

## Advanced report and export commands

The following stable commands are intentionally omitted from the short root help. They remain
available for automation and specialized output.

### `fermentation-brief`

Create a local Markdown report for one explicitly selected brew:

```bash
forge-companion fermentation-brief --select --temperature-unit C

forge-companion fermentation-brief BREW_ID \
  --output reports/fermentation-BREW_ID.md \
  --temperature-unit C
```

The interactive form normally uses one brew-list request and one readings request; every explicit
`n` or `p` adds another brew-list request. The UUID form uses one brew-detail request and one readings
request. The report includes the
observation period, gravity change, an optional 24-hour least-squares slope, temperature range,
reading freshness, largest telemetry gap, and recent readings.

Temperature units are never guessed. Omit `--temperature-unit` to label values as raw API values.

### `fermentation-csv`

Export accepted readings in chronological order:

```bash
forge-companion fermentation-csv --select
forge-companion fermentation-csv BREW_ID
forge-companion fermentation-csv BREW_ID --output reports/readings-BREW_ID.csv
```

The UUID form uses one readings request. The interactive form adds one brew-list request per
explicitly displayed page; additional pages are fetched only after explicit `n` or `p` input.

The stable columns are:

```text
id,timestamp_utc,gravity_sg,temperature_raw,pressure,ph,comment
```

Missing optional measurements remain empty. Text that spreadsheet applications could interpret as a
formula is prefixed with an apostrophe. The completion message reports rejected records and
conflicting timestamps; if no valid reading remains, no CSV is written.

### `fermentation-html`

Create a self-contained visual fermentation report using the legacy format-specific spelling:

```bash
forge-companion fermentation-html --select --temperature-unit C

forge-companion fermentation-html BREW_ID \
  --title "Lithuanian Session Witbier" \
  --temperature-unit C \
  --output reports/lithuanian-session-witbier.html
```

The recommended `report` command uses a smaller 25-item page. This legacy command retains its
100-item default plus explicit `--page` and `--limit` options. Both forms fetch another page only after
explicit input and fetch readings only after an explicit numbered choice.

The UUID form remains deterministic for scripts and uses exactly one readings request. It does not
fetch brew details; pass `--title` for a friendly name. Both forms include summary metrics,
data-quality evidence, recent readings, and an inline SVG gravity/temperature chart.

The HTML has no JavaScript, CDN, remote fonts, images, or tracking. Dynamic content is sanitized and
escaped, a restrictive Content Security Policy blocks external content, and writes are atomic.

## `spunding-advisor`

Simulate a fail-closed gravity threshold decision:

```bash
forge-companion spunding-advisor --select \
  --trigger-sg 1.0120

forge-companion spunding-advisor BREW_ID \
  --trigger-sg 1.0120 \
  --max-age-minutes 90 \
  --max-gap-minutes 120 \
  --confirmations 2
```

The UUID form uses one readings request. The interactive form makes one brew-list request per
explicitly displayed page and then requests only the selected brew's readings.

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

## `hopper`

Prepare and rehearse a remote-hop-dropper lifecycle entirely offline:

```bash
forge-companion hopper plan \
  --trigger-at 2026-08-01T18:00:00+00:00 \
  --pulse-ms 1500 \
  --brew-id BREW_ID

forge-companion hopper arm automation/hopper-plan.json
forge-companion hopper status automation/hopper-plan.json
forge-companion hopper simulate automation/hopper-plan.json
```

`plan` creates a `DRAFT` file and refuses to overwrite an existing destination. `arm` is a separate
explicit transition and only succeeds before the trigger time. `simulate` succeeds only for an
`ARMED` plan at or after the trigger and records this
ordered lifecycle before permanently setting that file to `LOCKED`:

```text
DRAFT -> ARMED -> FIRE_REQUESTED -> PULSE_ACTIVE -> VERIFIED_OFF -> LOCKED
```

All four commands are local. They do not resolve a BrewForge token, call an API, connect to a Shelly,
wait until the trigger time, or send a physical pulse. The optional brew UUID is metadata only.
`simulate --at TIME` provides an explicit clock for deterministic offline rehearsals; it is not a
scheduler and will not be part of any future hardware action.

Plan writes are atomic. Loading requires strict JSON, canonical UUIDs, UTC timestamps, an exact state
history, and a matching canonical SHA-256 digest. Invalid, modified, early, already-used, or
out-of-order plans fail closed without printing the file path or plan contents. The unkeyed digest is
change detection, not authentication; anyone able to replace the file can recompute it.

Plan creation and state transitions use a sibling `.PLAN_FILENAME.lock` file, so concurrent CLI
processes cannot both consume the same `ARMED` state. A hard process or machine crash can leave this
lock behind intentionally fail-closed. After confirming that no Forge Companion process is still
using the plan, remove only that sidecar lock before retrying; never replace the plan to bypass a
state validation failure.

The pulse value is required and bounded to 1–60,000 milliseconds so the plan is concrete, but this
range is **not** a hardware recommendation. A future actuator must use the measured winch runtime,
its own lower hard timeout, explicit device identity, state read-back, mechanical end protection, and
manual override. Until that separate feature exists, `ARMED`, `PULSE_ACTIVE`, `VERIFIED_OFF`, and
`LOCKED` describe only a simulation file.

Read one local Shelly switch channel separately:

```bash
forge-companion hopper shelly-status \
  --device-url http://192.0.2.1 \
  --channel 0
```

`shelly-status` makes exactly one local `GET /rpc/Switch.GetStatus?id=CHANNEL` request. It has no
generic RPC entry point, no `Switch.Set` method, no scheduler, and no connection to hopper-plan state.
It does not resolve a BrewForge token and never prints the device URL in status or error output.
Only a bare `http://` or `https://` device base URL without credentials, path, query, or fragment is
accepted. The current adapter does not implement Shelly authentication; an authenticated device will
fail closed until separate credential support exists.

`Output: OFF` confirms only the Shelly's reported electrical relay state. It does not prove that a
winch is isolated, that a hopper moved, or that a mechanical endpoint was reached. No pulse duration
from an LED test is a safe motor-runtime recommendation.

## API scopes

Use the narrowest scopes needed for your task:

- `brews:read`
- `inventory:read`
- `equipment:read`
- `styles:read`

Reports may contain private brew names, comments, identifiers, and measurements. Keep them in the
gitignored `reports/` directory unless you have reviewed them for sharing.
