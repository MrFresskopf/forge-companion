# Compatibility policy

> [!IMPORTANT]
> This is the pre-1.0 compatibility design for Forge Companion. Version 0.2.1 does not yet implement
> the planned `--json` options. The policy becomes binding for the stable scope when 1.0 is released;
> until then, incompatible changes remain possible when they are documented in the changelog.

Forge Companion follows semantic versioning for its documented, non-experimental public surface. The
1.0 goal is a stable read-only BrewForge core, not completion of every roadmap capability.

## Stability tiers

| Tier | Surface | Compatibility promise from 1.0 |
| --- | --- | --- |
| Stable | Authentication, diagnostics, brew selection, reports, exports, snapshots, validation, and inventory audits | Breaking changes require a major version or a documented deprecation path. |
| Versioned data | JSON command output, snapshot files, CSV columns, and simulated-pulse plans | Readers retain documented old-version support; incompatible formats receive a new schema or format identifier. |
| Experimental | Cloud-pulse plan creation and lifecycle, live qualification, and `hopper fire` | Command shape and qualification requirements may change; safety checks remain fail-closed. |
| Internal | Python modules, functions, classes, configuration paths, and keyring implementation details | No compatibility promise unless separately documented. |

Offline simulated-pulse planning and simulation may enter the stable tier independently of live
actuation. Cloud-pulse plan fields and transitions remain experimental together with the actuator. An
experimental actuator is not evidence that a pulse reached or released hops mechanically.

## Semantic versioning

- Patch releases fix defects, improve human wording, and may tighten validation for input already
  outside the documented contract without removing a supported successful workflow.
- Minor releases may add commands, options, JSON object members, finding codes, or report sections.
  New collections require a contract that already permits them or a new schema or format identifier.
  Consumers must ignore unknown JSON object members and unknown advisory finding codes.
- Major releases may remove or rename documented commands, options, fields, formats, or established
  meanings after migration guidance.
- Security fixes may additionally reject previously accepted input when continuing to accept it would
  violate a documented safety, privacy, or integrity invariant.

A versioned format identifier takes precedence over the package version. Package upgrades must not
silently reinterpret an existing file under the same format identifier.

## CLI contract

### Stable command candidates

The following documented command families are candidates for the 1.0 stable tier:

- `auth login`, `auth status`, and `auth logout`
- `doctor`
- `report`, `fermentation-brief`, `fermentation-csv`, and `fermentation-html`
- `brews`
- `snapshot` and `snapshot validate`
- `inventory`
- `spunding-advisor`
- offline `hopper plan`, `hopper arm`, `hopper simulate`, and `hopper status` for `simulated-pulse`
  plans
- local credential management through `hopper cloud-auth`
- read-only `hopper shelly-status` and `hopper cloud-status` diagnostics

Hidden legacy spellings remain compatibility aliases only while documented as such. New scripts should
use the preferred spelling. Cloud-pulse creation, arming, status, the online `hopper check`, and
`hopper fire` remain experimental even when shipped in a 1.x package. `hopper check` performs exactly
one observational Shelly Cloud status request; it is not an offline command.

### Options and arguments

From 1.0 onward:

- existing documented successful invocations keep their meaning throughout 1.x;
- new optional flags and new commands are compatible additions;
- making an optional value required, changing a default with behavioral impact, or changing an option's
  type requires deprecation or a major release;
- exact identifiers remain exact: Forge Companion does not silently convert names into BrewForge UUIDs;
- invalid local input is rejected before credential, filesystem, network, or actuator access whenever
  the command has enough information to do so;
- non-interactive commands never gain an implicit prompt or hidden pagination request.

### Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | The command completed according to its contract. Advisory inventory findings and simulation statuses such as `WAIT` are successful command results. |
| `1` | The invocation was understood, but an operational, integrity, validation, API, filesystem, credential-store, or domain failure prevented successful completion. `doctor` also uses 1 when one or more endpoint checks fail. |
| `2` | The invocation or required local precondition is invalid, including CLI parser errors, malformed local option values, and missing BrewForge authentication for a command that requires it. |

Additional nonzero codes require documentation before use. Scripts must not infer finer failure causes
from human-readable text; machine-readable error codes are the stable discriminator.

### Streams and human output

- Successful human-readable output goes to standard output.
- Errors and usage guidance go to standard error where supported by the CLI framework.
- Human wording, spacing, colors, and help layout are not machine contracts.
- Redirected output remains complete without ANSI sequences or a TTY requirement.
- Raw credentials, authorization headers, private custom paths, remote exception strings, and
  attacker-controlled terminal sequences are never reflected in errors.

## Planned machine-readable output

`doctor --json` and `inventory --json` will emit one UTF-8 JSON object followed by a newline and no
other standard-output text. Their versioned draft schemas are:

- [`doctor-v1.schema.json`](schemas/doctor-v1.schema.json)
- [`inventory-audit-v1.schema.json`](schemas/inventory-audit-v1.schema.json)

The schemas use additive evolution: consumers must ignore unknown object members. Removing a required
member, changing a member's type or meaning, or reusing an established code for a different condition
requires a new schema identifier.

Schemas validate the public structure, not the absence of sensitive values in unknown future members.
Implementations use allowlisted output serializers and recursive privacy tests; schema validity alone
is never treated as proof that output is safe to share.

A recognized command that reaches JSON execution emits the command schema for both successful and
operationally failed outcomes. Errors rejected earlier by the CLI parser may remain plain usage output
with exit code 2, even when the original arguments contained `--json`.

Common rules:

- `schema` identifies the contract independently of the package version.
- `generated_at` is an RFC 3339 UTC timestamp ending in `Z`.
- `status` is `ok`, `partial`, or `error` where allowed by the command schema.
- `errors[].code` is stable; `errors[].message` is explanatory and may improve.
- Known arrays have deterministic order.
- JSON mode contains no ANSI control sequences.
- Error objects never contain credentials, raw transport exceptions, remote response bodies, private
  custom paths, or local keyring details.

### Doctor v1

Doctor v1 preserves explicit request accounting and one result per documented collection. A complete
seven-endpoint run uses seven requests. Authentication or setup failure before client construction uses
zero requests and returns `status: "error"`. Endpoint failures return `status: "partial"`, retain all
seven checks, and use exit code 1.

Consumers use `checks[].code`, not `checks[].message`, for decisions. HTTP status is reported only as an
integer or `null`; response text and transport exception text are excluded.

Initial endpoint codes are `ok`, `http-error`, `request-failed`, and `invalid-response`. Initial
command-level setup codes are `authentication-not-configured`, `credential-invalid`, and
`credential-store-failed`. New codes are compatible additions when they retain the same privacy rules.
Missing authentication returns the structured error with exit code 2; invalid credentials or credential
store failure use exit code 1.

### Inventory audit v1

Inventory v1 is always offline and therefore declares `request_count: 0`. Findings are advisory data,
so any number or severity of findings still returns `status: "ok"` and exit code 0. Failure to validate
or read the snapshot returns `status: "error"` and exit code 1. A malformed `--as-of` value is a local
invocation error: JSON mode emits `invalid-as-of` and exits 2 after argument parsing but before snapshot
access.

Finding names, IDs, and messages are private inventory data, not sanitized shareable telemetry. Custom
snapshot paths and snapshot records are not included. Consumers use `findings[].code`, severity, and
structured identifiers rather than parsing `message`.

`possible-duplicate` includes non-empty, distinct `item_id` and `related_item_id` values for the two
matching items. The implementation must add and test that structured relationship before inventory JSON
v1 can leave draft status. A successful audit always reports the effective `as_of` date; only an error
produced before date resolution may use `null`. The schema enforces this chronology for the initial
known error codes; future producers must classify and test new codes before adding them.

Initial finding codes are `expired`, `negative-quantity`, `missing-unit`, and `possible-duplicate`.
Initial command-level error codes are `invalid-as-of`, `snapshot-not-found`, `snapshot-invalid`, and
`snapshot-read-failed`. New finding codes are compatible additions and remain advisory.

## Snapshot compatibility

`forge-companion-collection-snapshot-v2` is strict persisted input:

- its field set, canonicalization algorithm, and digest calculation do not change in place;
- 1.x readers continue to validate valid v2 snapshots;
- inventory audits continue to accept strict legacy v1 snapshots with no integrity claim;
- a structurally incompatible writer uses a new format identifier;
- a new writer format ships with documented migration or overlapping read support before it becomes the
  default;
- unsupported, ambiguous, or integrity-invalid snapshots fail closed without displaying their custom
  path or records.

Canonical v2 integrity uses a deep copy of the complete payload, removes only
`manifest.integrity.digest`, serializes UTF-8 JSON with Unicode preserved, object keys sorted,
`,` and `:` separators without extra whitespace, and non-finite numbers rejected, then stores the
lowercase SHA-256 hex digest. A normative fixture must be published before the 1.0 freeze.

The SHA-256 digest detects changes. It is not authentication, proof of BrewForge origin, encryption, or
access control. Full-export additions must identify exclusions and rate-limit behavior explicitly.
Restore and BrewForge writes remain outside the 1.0 contract.

## CSV and report formats

The documented fermentation CSV header is a stable ordered contract:

```text
id,timestamp_utc,gravity_sg,temperature_raw,pressure,ph,comment
```

The header remains exact throughout 1.x. Additional measurements require a new explicitly selected CSV
format or a major release; they are not silently appended to the existing automation contract.

Markdown and HTML reports are human-facing documents. Their visual layout and prose may evolve, while
security invariants remain stable: escaped dynamic content, no hidden network dependencies in standalone
HTML, atomic writes, and no credential material sourced or managed by Forge Companion itself.

## Credentials and local configuration

The exact keyring service/account names and configuration file location are implementation details.
The supported behavior is stable:

- normal authentication uses a native OS credential store;
- `BREWFORGE_API_TOKEN` is an explicit BrewForge-only override for CI and temporary sessions;
- Shelly Cloud credentials have no environment or plaintext fallback;
- upgrades preserve supported stored preferences or migrate them explicitly;
- credentials resolved or managed by Forge Companion are never intentionally serialized into snapshots,
  reports, automation plans, JSON diagnostics, or logs.

Exports still contain private BrewForge- and user-controlled values such as names, comments, IDs, and
measurements. Those values could themselves contain secret-like text. Generated files are private by
default and must be reviewed before sharing; the credential guarantee does not make exports public-safe.

## Deprecation process

A stable public surface is deprecated before removal whenever a security emergency does not require an
immediate fail-closed change:

1. document the replacement and first deprecated version in the changelog and command guide;
2. retain the old behavior for at least the remainder of the current major release;
3. emit warnings only on standard error and never corrupt JSON or file output;
4. remove the surface only in the next major release.

The 1.0 release notes will list every stable surface, experimental exception, supported snapshot reader,
and migration from the final 0.x release.
