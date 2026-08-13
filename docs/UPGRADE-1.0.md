# Upgrade from 0.x to 1.0

> [!IMPORTANT]
> Forge Companion 1.0 is not released yet. This guide defines the supported transition that the
> `1.0.0rc1` candidate must exercise. Do not install a nonexistent `v1.0.0` tag; use these commands only
> after that immutable tag has been published.

Forge Companion 1.0 stabilizes the offline Shelly planning core, read-only device diagnostics, and
supporting read-only BrewForge tools. It does **not** promote live Shelly actuation to stable status.
Cloud-pulse planning, qualification, readiness checks, and `hopper fire` remain experimental.

## Before upgrading

1. Finish or deliberately abandon any active local plan. Do not edit a plan to make it reusable.
2. Copy private `automation/`, `snapshots/`, and `reports/` files to protected storage if they matter to
   you. These paths are normally gitignored and are not part of the package installation.
3. Run the current release's offline validators where applicable:

   ```bash
   forge-companion snapshot validate snapshots/brewforge-collections.json
   forge-companion hopper status automation/hopper-plan.json
   ```

4. Record which credentials are configured without displaying them:

   ```bash
   forge-companion auth status
   forge-companion hopper cloud-auth status
   ```

Native credential-store entries and the non-secret preferences file are not removed by a normal tool
upgrade. Keep an independent credential recovery path anyway; neither snapshots nor plans contain API
keys.

## Install the immutable release

After the final release tag exists, install that tag explicitly:

```bash
uv tool install --force git+https://github.com/MrFresskopf/forge-companion.git@v1.0.0
```

Or with pipx:

```bash
pipx install --force git+https://github.com/MrFresskopf/forge-companion.git@v1.0.0
```

Confirm the installed entry point, then run read-only checks:

```bash
forge-companion --version
forge-companion auth status
forge-companion doctor --json
forge-companion hopper cloud-auth status
```

A completed `doctor --json` endpoint run performs three read-only BrewForge GET requests. Setup failures,
including missing or invalid authentication and local client setup errors, perform none. `auth status`
and `hopper cloud-auth status` are offline. Run device diagnostics only when you intentionally want one
observational request.

## Public contracts entering 1.0

The [`cli-v1-contract.json`](../src/forge_companion/contracts/cli-v1-contract.json) file is an
executable product manifest packaged with Forge Companion. It freezes command paths, hidden status,
arguments, options, defaults, basic parameter ranges, exit-code classes, and stable/experimental
classification. Framework-owned help and shell-completion options remain outside this manifest. The test
suite compares it with the registered Typer application.

The stable 1.x surface includes:

- BrewForge and Shelly Cloud credential management behavior;
- `doctor`, including the closed `forge-companion-doctor-v2` JSON document;
- `brews`, reports, fermentation exports, snapshots, and snapshot validation;
- `spunding-advisor` as simulation only;
- local and Cloud read-only Shelly status;
- offline `simulated-pulse` plan creation, arming, simulation, and status.

The following remain experimental even in a 1.x package:

- `hopper plan --cloud` and every `cloud-pulse` plan lifecycle;
- `hopper qualification ...`;
- `hopper check`;
- `hopper fire` and its live actuator boundary.

The `hopper plan`, `hopper arm`, and `hopper status` command paths are mixed: their simulated-pulse mode
is stable, while their cloud-pulse mode is experimental. Experimental command shape and qualification
requirements may change without a major release, but safety checks remain fail-closed and changes are
listed in the changelog.

## Persisted data

### Collection snapshots

Forge Companion 1.0 reads and writes only `forge-companion-collection-snapshot-v3`. Historical v1 and
v2 snapshots are rejected rather than silently modified, truncated, or re-signed. There is no automatic
migration command.

- Keep a historical v1/v2 file unchanged if it matters.
- Validate it only with the matching historical release in an isolated environment.
- When BrewForge remains available, create a fresh v3 snapshot with the current release.
- Never rename an old format to v3 or recompute its digest to make it appear current.

### Hopper plans

A valid `simulated-pulse` plan retains its documented format and strict digest/state-history checks.
Cloud-pulse plans remain experimental. Do not carry an armed live plan across an upgrade: complete,
cancel operationally, or preserve it for audit and create a fresh plan after re-checking the mechanism,
credentials, trigger, and pulse assumptions.

### Preferences and credentials

The native keyring service/account names and preference path are implementation details. Supported
upgrades preserve valid stored credentials and non-secret preferences or provide an explicit migration.
Forge Companion never falls back to a plaintext credential file.

A hopper qualification attestation is not transferable evidence after the magnet, cable routing, vessel
geometry, winch, load, direction, endpoint, pulse assumption, or auto-off changes. Revoke it and repeat
the complete qualification process.

## Exit codes and automation

The 1.x CLI uses these stable classes:

| Code | Meaning |
| ---: | --- |
| `0` | The command completed according to its contract. |
| `1` | An operational, integrity, application-level validation, API, filesystem, credential-store, or domain failure prevented completion. This includes values parsed by the CLI but rejected by command validation. |
| `2` | The CLI parser rejected syntax, a required option, or a parser-enforced constraint; or a command explicitly documents this class for missing setup. Current non-parser cases are missing required BrewForge authentication and a missing Shelly Cloud profile for `hopper cloud-status`. Experimental live-actuation commands may classify missing Cloud setup as `1`. |

Human wording, spacing, color, and help layout are not automation contracts. Scripts should use exact
exit codes, documented file formats, the Doctor schema, and pinned UUIDs rather than parse human prose.

## Support window

Once 1.0.0 is released:

- the newest 1.x minor receives normal correctness and security fixes;
- the immediately previous 1.x minor receives security fixes for 90 days after its successor is
  released;
- 0.x receives security fixes for 90 days after 1.0.0, then becomes unsupported;
- release candidates are evaluation builds and have no maintenance window;
- experimental live-actuation behavior is outside the compatibility promise at every version.

This is a best-effort community project with no response-time or remediation SLA. A security issue may
require immediate fail-closed rejection of previously accepted unsafe input.

## Security scope

Supported security reports include credential exposure, authorization bypass, unsafe crossing of the
read-only BrewForge boundary, snapshot or plan validation bypass, terminal/control-sequence injection,
unauthorized or repeat Shelly actuation, and failure of the documented durable one-shot boundary.

Out of scope as software guarantees are BrewForge or Shelly provider availability, compromised provider
accounts, physical tampering, lost local account access, electrical or mechanical faults, magnet/cable
release success, pressure safety, and damage caused by bypassing documented mechanical safeguards.
Electrical `OFF` is telemetry, not proof of motion or hop release.

Report suspected vulnerabilities privately through the process in [`SECURITY.md`](../SECURITY.md).

## Rollback

Reinstall the prior immutable tag with the same `uv tool install --force ...@TAG` or
`pipx install --force ...@TAG` pattern. Do not ask an older release to reinterpret files written under a
new format identifier. Preserve the newer files separately and use the reader documented for their
format.
