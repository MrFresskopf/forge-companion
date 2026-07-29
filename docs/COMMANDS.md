# Command guide

Forge Companion uses a supported native OS credential store by default. `BREWFORGE_API_TOKEN` remains
an explicit override for CI, scripts, and temporary sessions. Shelly Cloud uses a separate native
profile with no environment or plaintext fallback. The inventory audit is the exception: it only
reads a local snapshot and needs no credential.

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
forge-companion doctor --json
```

The command checks brews, inventory, equipment, and style profiles with seven API requests. Each
endpoint reports `OK` or `FAIL` independently.

`--json` emits exactly one compact JSON document on standard output after CLI parsing succeeds. It uses
the closed `forge-companion-doctor-v1` contract defined by the packaged
[`doctor-v1.schema.json`](../src/forge_companion/schemas/doctor-v1.schema.json). The document always has
`schema_version`, `status`, `checks`, and `error` fields:

- `status: "ok"` contains seven successful checks and exits `0`.
- `status: "failed"` contains all seven ordered checks, at least one failed check, and exits `1`.
- `status: "error"` contains no checks. Missing authentication uses
  `authentication_required` and exits `2`; invalid environment credentials, invalid stored
  credentials, native credential-store failures, and local HTTP-client setup failures use fixed codes
  and exit `1`. HTTP-client setup failures use `client_setup_error`.

Each endpoint check contains only its documented path, `ok` or `failed`, an HTTP status or `null`, and
a fixed error code or `null`. Response bodies, exception text, credentials, and raw API data are never
included. Setup errors make no API request; completed diagnostics still make exactly seven. Invalid
CLI syntax is rejected by Typer before the command runs and therefore remains a normal human-readable
usage error rather than a doctor-v1 document.

The v1 schema is closed: unknown fields, endpoint reordering, contradictory status/error combinations,
and unsupported error codes are invalid. A future incompatible shape requires a new schema version;
consumers should select behavior by the exact `schema_version` value.

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
> This is not yet a complete or restorable account backup. Version 0.2 does not fetch per-brew
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

The default hopper workflow remains an offline rehearsal:

```bash
forge-companion hopper plan \
  --trigger-at 2026-08-01T18:00:00+00:00 \
  --pulse-ms 1500 \
  --brew-id BREW_ID
forge-companion hopper arm automation/hopper-plan.json
forge-companion hopper simulate automation/hopper-plan.json
forge-companion hopper status automation/hopper-plan.json
```

`plan` creates a `DRAFT` file and refuses to overwrite an existing destination. `arm` is a separate
explicit transition and only succeeds before the trigger time. `simulate` succeeds only for an
`ARMED` `simulated-pulse` plan at or after the trigger and records the complete lifecycle without
contacting hardware. It rejects Cloud one-shot plans:

```text
DRAFT -> ARMED -> FIRE_REQUESTED -> PULSE_ACTIVE -> VERIFIED_OFF -> LOCKED
```

`simulate --at TIME` is available only for deterministic offline rehearsal. It is not a scheduler and
is never accepted by the live fire command.

Plan writes are atomic. Loading requires strict JSON, canonical UUIDs, UTC timestamps, an exact state
history, and a matching canonical SHA-256 digest. Invalid, modified, early, already-used, or
out-of-order plans fail closed without printing the file path or plan contents. The unkeyed digest is
change detection, not authentication; anyone able to replace the file can recompute it.

Plan transitions use an exclusive sibling `.PLAN_FILENAME.lock` file. A hard crash can leave this
lock behind intentionally fail-closed. Remove it only after confirming no Forge Companion process is
still using the plan. Never replace a plan to bypass state validation.

### Shelly Cloud one-shot

Configure and verify the native Shelly Cloud profile first:

```bash
forge-companion hopper cloud-auth login
forge-companion hopper cloud-auth status
forge-companion hopper cloud-status --channel 0
```

Then create and arm a plan bound to that stored tenant and device. The authorization key is not copied
into the plan:

```bash
forge-companion hopper plan --cloud \
  --trigger-at 2026-08-01T18:00:00+00:00 \
  --pulse-ms 1500 \
  --output automation/hopper-plan.json
forge-companion hopper arm automation/hopper-plan.json
forge-companion hopper status automation/hopper-plan.json
```

After the trigger, rehearse the complete read-only readiness path without arming, modifying the plan,
or constructing an actuator:

```bash
forge-companion hopper check automation/hopper-plan.json
```

`check` validates an `ARMED` Cloud plan, confirms that its trigger has been reached, verifies that the
native credential profile matches the digest-protected target, and sends exactly one observational
Cloud status POST. Success requires `online` and explicit electrical `OFF`. It prints no host, device
ID, key, plan path, or provider response text. The plan remains byte-for-byte unchanged and no switch
request is sent. A passing result is temporary electrical readiness only: it does not authorize a later
pulse, replace the fresh preflight inside `fire`, or prove mechanical release.

After the trigger time, the live command is:

```bash
forge-companion hopper fire automation/hopper-plan.json
```

`fire` validates the plan and requires exact interactive `FIRE` confirmation on an attached terminal;
piped or redirected input is rejected. Only after confirmation does it resolve the matching native
credential and perform a fresh read-only preflight. The device must be online and explicitly report
electrical `OFF`; otherwise the command stops without a switch request. It then observes the provider's
one-second request boundary and atomically persists `FIRE_REQUESTED` immediately before the single
switch attempt. The actuator sends exactly one Cloud v2
`POST /v2/devices/api/set/switch` for fixed channel 0 with `on: true` and the bounded
`toggle_after` value. Only HTTP 200 is accepted for the set request; its response body is not read or
interpreted, and the later size-capped status read-back determines whether the plan can lock. There is
no automatic retry. The Cloud profile key is not persisted outside the native credential store; it is
held in memory only as needed and transmitted only to the assigned Shelly Cloud host.

After the device timer should have expired, the actuator waits at least one second to respect the
provider request-rate boundary and performs one status read-back. Only an online, explicit electrical
`OFF` result completes `PULSE_ACTIVE -> VERIFIED_OFF -> LOCKED`. A rejected, timed-out, malformed,
offline, or still-ON result leaves the durable plan at `FIRE_REQUESTED`; the outcome is ambiguous and
must not be retried automatically.

Cloud pulse duration is limited to 1–30,000 ms. This is a software ceiling, not a safe runtime
recommendation. Use only a measured under-load runtime plus a conservative margin, device-side
auto-off as an independent backstop, mechanical endpoint protection, and a manual isolation method.
The command does not wait for the trigger, schedule itself, re-arm a used plan, or infer success from a
transport timeout.

An electrical `OFF` read-back proves neither winch travel nor magnet release nor actual hop addition.
Qualify the complete Fermzilla, magnet, cable, and winch assembly repeatedly before relying on remote
operation. One explicit confirmation authorizes one attempt only. Record full-assembly evidence with
the [mechanical qualification protocol](HOPPER_QUALIFICATION.md) and its CSV template; the protocol
does not calculate or certify a safe duration.

### Read-only Shelly diagnostics

Read one local switch channel without changing it:

```bash
forge-companion hopper shelly-status \
  --device-url http://192.0.2.1 \
  --channel 0
```

The local diagnostic exposes only `GET /rpc/Switch.GetStatus`. It has no generic RPC entry point,
write method, or connection to the Cloud actuator. Responses are streamed under 64 KiB, environment
proxies are ignored, and owned HTTP clients are closed.

The separate `cloud-status` command sends one observational POST to Cloud v2
`/v2/devices/api/get`, selecting only `status.switch:CHANNEL`. It never calls the set endpoint. Only a
canonical `*.shelly.cloud` host and 12-hex device ID are accepted; redirects, inherited proxies,
oversized or ambiguous JSON, mismatched identities, and stale offline output fail closed.

Shelly Cloud uses the device's existing outbound provider connection, so no router port forwarding,
public local RPC endpoint, or VPN is required. Never expose the local Shelly RPC interface to the
Internet.

## API scopes

Use the narrowest scopes needed for your task:

- `brews:read`
- `inventory:read`
- `equipment:read`
- `styles:read`

Reports may contain private brew names, comments, identifiers, and measurements. Keep them in the
gitignored `reports/` directory unless you have reviewed them for sharing.
