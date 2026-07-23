<p align="center">
  <img src="docs/assets/forge-companion-hero.svg" alt="Forge Companion: understand your BrewForge data and leave it untouched" width="100%">
</p>

<p align="center">
  <a href="https://github.com/MrFresskopf/forge-companion/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/MrFresskopf/forge-companion/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white"></a>
  <a href="LICENSE"><img alt="MIT license" src="docs/assets/badges/license.svg"></a>
  <img alt="BrewForge access" src="docs/assets/badges/brewforge-read-only.svg">
</p>

Forge Companion turns [BrewForge](https://brewforge.sh/) data into local snapshots,
inventory checks, CSV exports, and fermentation reports. It never creates, changes, or deletes
anything in BrewForge.

> [!IMPORTANT]
> Forge Companion is an unofficial community project and is not affiliated with or endorsed by
> BrewForge.

> [!NOTE]
> **Developer preview:** Forge Companion is source-installable, read-only, and not yet a complete
> backup solution. Interfaces and snapshot formats may change before 1.0.

## Get running in three steps

You need Python 3.11 or newer, a BrewForge plan with API access, and a token with the narrowest
read scopes needed for your command.

### 1. Install

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install git+https://github.com/MrFresskopf/forge-companion.git@main
```

Or with [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/MrFresskopf/forge-companion.git@main
```

### 2. Store your token securely

Use the native Windows Credential Manager, macOS Keychain, or Linux Secret Service:

```bash
forge-companion auth login
forge-companion auth status
```

The token prompt is hidden and confirmed. Forge Companion refuses unavailable or non-native keyring
backends instead of falling back to a plaintext credential file. For CI and temporary overrides, a
valid `BREWFORGE_API_TOKEN` remains supported and takes precedence over the stored credential.
Whitespace-only values are ignored; values containing whitespace are rejected and block stored
credential use until corrected or unset. Do not put a real token in a config file, issue, screenshot,
command argument, or commit.

### 3. Create your first report

```bash
forge-companion report --temperature-unit C --remember
```

In an interactive terminal, `report` shows 25 sanitized brew names at a time and waits for an
explicit choice. Enter a number to select a brew, `n` or `p` to change pages, or `q` to cancel.
`--remember` stores only the non-secret
temperature-unit preference; API tokens remain in the native credential store. The chosen name
becomes the report title and one standalone HTML file is written to `reports/`.

For scripts and pipelines, an exact UUID is required; automatic prompting never starts on
non-interactive input. Run `forge-companion doctor` only when you want to check every
documented collection and token scope.

<p align="center">
  <img src="docs/assets/fermentation-report.png" alt="Example standalone Forge Companion fermentation report" width="880">
</p>

## What it does

| Goal | Command | Network use |
|---|---|---:|
| Store or inspect authentication | `forge-companion auth ...` | Offline |
| Create the standard visual report | `forge-companion report` | 2 GET requests + explicit page changes |
| Create a scripted report | `forge-companion report BREW_ID` | 1 GET request |
| Save supported collections locally | `forge-companion snapshot` | Paginated GET requests |
| Verify the standard snapshot | `forge-companion snapshot validate` | Offline |
| Check inventory from the standard snapshot | `forge-companion inventory` | Offline |
| Diagnose API access | `forge-companion doctor` | 7 GET requests |
| Simulate a spunding threshold | `forge-companion spunding-advisor --select ...` | 2 GET requests + explicit page changes |
| Prepare and rehearse a remote hopper | `forge-companion hopper ...` | Offline |

Markdown, CSV, UUID listing, custom snapshot paths, and deterministic legacy command names remain
available for advanced use and scripts. See the [command guide](docs/COMMANDS.md) for details.

## Why read-only?

Brewing data is useful; accidental writes are not. Forge Companion starts with a deliberately small
trust boundary:

- the API client exposes only `GET`
- tokens come from a supported native OS credential store or an explicit `BREWFORGE_API_TOKEN`
  environment override
- report preferences contain no credentials and currently store only an explicit C/F choice
- default `reports/` and `snapshots/` destinations stay local and are ignored by Git; custom output
  paths remain your responsibility
- collection snapshots abort on invalid or incomplete pages; v2 snapshots include collection counts,
  explicit scope exclusions, and a canonical SHA-256 integrity digest
- `snapshot validate` rejects malformed, ambiguous, unsupported, or modified v2 files offline;
  fermentation exports keep valid readings but report every rejection and timestamp conflict
- the spunding advisor simulates a decision and never contacts hardware
- hopper plans, arming, status checks, and lifecycle rehearsals are offline; no device client or
  physical pulse path exists

The generated HTML report is one offline file with no JavaScript, remote fonts, tracking, or external
assets. It describes telemetry but does not decide that fermentation is complete.

## Install for development

```bash
git clone https://github.com/MrFresskopf/forge-companion.git
cd forge-companion
uv sync --extra dev
uv run forge-companion --help
```

Run the quality checks before opening a pull request:

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

## Project status

Forge Companion is young and intentionally conservative. Collection snapshots, inventory audits,
fermentation exports/reports, fail-closed spunding simulations, and offline remote-hopper rehearsals
work today. MQTT, Home Assistant, Shelly connectivity, and physical hardware actions remain future
work.

The snapshot command currently covers supported top-level collections. Its checksum detects accidental
or deliberate file changes, but it is not a signature, proof of origin, or encryption. A snapshot is
not yet a complete or restorable account backup. See the [roadmap](docs/ROADMAP.md) for current scope
and non-goals.

## Contributing

Small, test-backed changes are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a
pull request, and never include private brew data or real API tokens in fixtures, screenshots, issues,
or commits.

Security reports belong in the private process described in [SECURITY.md](SECURITY.md).

If Forge Companion is useful and you are considering BrewForge, you can
[support the project with this referral link](https://brewforge.sh/r/ckpejh7o). The destination is
the normal BrewForge service; the link credits this project when you sign up.

## License

MIT. See [LICENSE](LICENSE).
