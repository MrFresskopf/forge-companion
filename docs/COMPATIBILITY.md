# Compatibility policy

> [!IMPORTANT]
> This is the pre-1.0 compatibility design for Forge Companion. Version 0.3.0 implements the documented
> `doctor --json` contract while live Cloud actuation remains experimental.
> The policy becomes binding for the stable scope when 1.0 is released; until then, incompatible changes
> remain possible when they are documented in the changelog.

The executable
[`cli-v1-contract.json`](../src/forge_companion/contracts/cli-v1-contract.json) records the current
1.0 freeze candidate: command paths, hidden status, arguments, options, defaults, basic parameter ranges,
and stable, mixed, or experimental classification. Framework-owned help and shell-completion options
are intentionally excluded because their spelling and presentation follow the supported Typer version.
Tests compare the manifest with the registered CLI. The [0.x-to-1.0 upgrade guide](UPGRADE-1.0.md)
defines migration, support, security, rollback, and the experimental live-actuation boundary.

Forge Companion follows semantic versioning for its documented, non-experimental public surface. The
1.0 goal is a stable safety core for Shelly planning and diagnostics, with supporting read-only
BrewForge reports, not completion of every roadmap capability.

## Stability tiers

| Tier | Surface | Compatibility promise from 1.0 |
| --- | --- | --- |
| Stable | Offline hopper planning, read-only Shelly diagnostics, authentication, BrewForge diagnostics, reports, exports, snapshots, and validation | Breaking changes require a major version or a documented deprecation path. |
| Versioned data | JSON command output, snapshot files, CSV columns, and simulated-pulse plans | Readers retain documented old-version support; incompatible formats receive a new schema or format identifier. |
| Experimental | Cloud-pulse plan creation and lifecycle, live qualification, and `hopper fire` | Command shape and qualification requirements may change; safety checks remain fail-closed. |
| Internal | Python modules, functions, classes, configuration paths, and keyring implementation details | No compatibility promise unless separately documented. |

Offline simulated-pulse planning and simulation may enter the stable tier independently of live
actuation. Cloud-pulse plan fields and transitions remain experimental together with the actuator. An
experimental actuator is not evidence that a pulse reached or released hops mechanically.

## Semantic versioning

- Patch releases fix defects, improve human wording, and may tighten validation for input already
  outside the documented contract without removing a supported successful workflow.
- Minor releases may add commands, options, report sections, and members or finding codes where a
  versioned schema explicitly permits additive evolution. Closed schemas require a new schema identifier
  for any shape or code outside their declared set. New collections require a contract that already
  permits them or a new schema or format identifier.
- Major releases may remove or rename documented commands, options, fields, formats, or established
  meanings after migration guidance.
- Security fixes may additionally reject previously accepted input when continuing to accept it would
  violate a documented safety, privacy, or integrity invariant.

A versioned format identifier takes precedence over the package version. Package upgrades must not
silently reinterpret an existing file under the same format identifier.

## CLI contract

### Stable command candidates

The following documented command families are included in the 1.0 stable freeze candidate:

- `auth login`, `auth status`, and `auth logout`
- `doctor`
- `report`, `fermentation-brief`, `fermentation-csv`, and `fermentation-html`
- `brews`
- `snapshot` and `snapshot validate`
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
| `0` | The command completed according to its contract. Simulation statuses such as `WAIT` are successful command results. |
| `1` | The invocation was understood, but an operational, integrity, application-level validation, API, filesystem, credential-store, or domain failure prevented successful completion. This includes values parsed successfully by the CLI but rejected by command validation. `doctor` also uses 1 when one or more endpoint checks fail. |
| `2` | The CLI parser rejected syntax, a required option, or a parser-enforced value constraint; or a command explicitly defines a missing setup prerequisite in this class. Current non-parser cases are missing BrewForge authentication for commands that require it and a missing Shelly Cloud profile for `hopper cloud-status`. Experimental live-actuation commands may instead classify missing Cloud setup as an application/domain failure (`1`). |

Additional nonzero codes require documentation before use. Scripts must not infer finer failure causes
from human-readable text; machine-readable error codes are the stable discriminator.

### Streams and human output

- Successful human-readable output goes to standard output.
- Errors and usage guidance go to standard error where supported by the CLI framework.
- Human wording, spacing, colors, and help layout are not machine contracts.
- Redirected output remains complete without ANSI sequences or a TTY requirement.
- Raw credentials, authorization headers, private custom paths, remote exception strings, and
  attacker-controlled terminal sequences are never reflected in errors.

## Machine-readable output

`doctor --json` is governed by the closed packaged
[`doctor-v2.schema.json`](../src/forge_companion/schemas/doctor-v2.schema.json). It emits one compact
UTF-8 JSON object followed by a newline and no other standard-output text after CLI parsing succeeds.

The Doctor schema is closed: consumers reject unknown object members, codes, and endpoint order. Any
new Doctor member or code requires a new schema identifier.

Schemas validate the public structure, not the absence of sensitive values in future user-controlled
content. Implementations use allowlisted output serializers and recursive privacy tests; schema validity
alone is never treated as proof that output is safe to share. JSON mode contains no ANSI control
sequences, credentials, raw transport exceptions, remote response bodies, private custom paths, or local
keyring details.

A recognized command that reaches JSON execution emits its command schema for both successful and
operationally failed outcomes. Errors rejected earlier by the CLI parser remain plain usage output with
exit code 2, even when the original arguments contained `--json`.

### Doctor v2

Doctor v2 uses `schema_version: "forge-companion-doctor-v2"` and always includes `status`, `checks`, and
`error`. A completed three-endpoint run retains the fixed endpoint order and uses three requests:

- `status: "ok"` contains three successful checks and exits 0;
- `status: "failed"` contains three checks with at least one failure and exits 1;
- `status: "error"` contains no checks, performs no API request, and uses a structured setup error.

Each check contains only `path`, `status`, `http_status`, and `error_code`. Fixed endpoint error codes are
`http_error`, `request_error`, and `invalid_response`; successful checks use `null`. Fixed setup codes are
`authentication_required`, `client_setup_error`, `credential_store_error`,
`invalid_environment_credential`, and `invalid_stored_credential`. Missing authentication exits 2; the
other setup errors exit 1. Response text, raw API data, and exception text are excluded.

## Snapshot compatibility

`forge-companion-collection-snapshot-v3` is the only collection snapshot format written and accepted
by the current CLI. The validator is deliberately closed and fail-closed: an unknown, older, newer,
or structurally extended format is rejected before its records are used.

The normative synthetic example is
[`tests/fixtures/collection-snapshot-v3.json`](../tests/fixtures/collection-snapshot-v3.json). It
contains no live account data. Its fixed summary and canonical digest are exercised by the test suite.
The implementation in `forge_companion.backup.validate_backup` remains the executable validator.

### Frozen v3 envelope

A valid v3 snapshot has exactly these properties:

- top-level keys are exactly `format`, `created_at`, `manifest`, and `resources`;
- `format` is exactly `forge-companion-collection-snapshot-v3`;
- `created_at` is an ISO 8601 timestamp normalized to UTC and expressed with `Z` or a zero offset
  such as `+00:00`; naive timestamps and non-zero offsets are rejected;
- `manifest.generator` contains exactly `name` and `version`, with the name `forge-companion`;
- the generator version is informative and is not used as a reader-version gate;
- `manifest.collections` and `resources` contain exactly `brews`, `profiles_equipment`, and
  `profiles_styles`, and each declared count equals the corresponding array length;
- each collection item is a JSON object. Its BrewForge fields are opaque payload data rather than a
  promise that every upstream record field will remain unchanged;
- `manifest.excluded` is exactly `brew_details`, `brew_notes`, `brew_readings`, and
  `undocumented_resources` in that order;
- `manifest.integrity` uses SHA-256 and
  `json-sort-keys-compact-utf8-without-digest`; and
- the lowercase hexadecimal digest covers the complete snapshot after removing only the digest field,
  encoded as compact UTF-8 JSON with sorted object keys and non-finite numbers forbidden.

Whitespace and object-key order in the stored JSON are not contractual because canonicalization removes
those differences. Collection array order and all record values are contractual because they affect the
digest. Duplicate object keys, non-finite numbers, count mismatches, additional envelope fields, and a
wrong digest are rejected.

Changing the envelope keys, resource set, exclusions, integrity algorithm, canonicalization, timestamp
rule, or interpretation of collection records requires a new snapshot format identifier such as v4. A
new writer may continue to emit v3 only while it preserves all rules above. Maintained readers must keep
accepting valid v3 snapshots unless a documented security issue requires a coordinated exception.

### Historical formats and migration policy

| Format | Historical contents | Current CLI | Safe transition |
| --- | --- | --- | --- |
| v1 | Seven collections, including four inventory collections; no manifest or integrity digest | Rejected | Preserve the original as unverified historical data. Create a fresh v3 snapshot from BrewForge. |
| v2 | Seven collections, including four inventory collections; manifest, counts, exclusions, and SHA-256 digest | Rejected | Preserve and, if needed, validate with the matching historical Forge Companion release in an isolated environment. Create a fresh v3 snapshot from BrewForge. |
| v3 | Brews, equipment profiles, and style profiles with the frozen envelope above | Accepted | No migration required; `snapshot validate` is offline and read-only. |
| Unknown or future | Not defined by this release | Rejected | Upgrade only to software that explicitly documents and validates that format. |

There is currently no `snapshot migrate` command. Renaming v1/v2 to v3, deleting inventory keys, or
recomputing a digest does **not** establish provenance and is not a supported migration. Forge Companion
must never silently reinterpret, overwrite, truncate, or re-sign an older snapshot.

A future migration implementation must:

1. identify and validate the source with a dedicated version-specific reader before transformation;
2. declare every dropped, renamed, synthesized, or semantically changed field and collection;
3. write a separate destination without overwriting the source;
4. produce a new target-format digest only after the transformation succeeds;
5. validate the destination with the target-format reader; and
6. report source format, target format, losses, and destination without exposing record contents.

When BrewForge is still available, creating a fresh v3 snapshot is preferred to local conversion because
it re-reads the supported current resources rather than laundering an obsolete envelope. When BrewForge
is unavailable, retain the historical file unchanged; manual extraction is recovery work, not a v3
migration and not proof of snapshot integrity.

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
