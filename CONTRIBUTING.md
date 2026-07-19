# Contributing

Forge Companion is deliberately small, read-only, and fail-closed. Contributions are welcome when
they keep those properties intact.

## Before you start

Open an issue before building a large feature or anything that overlaps BrewForge's roadmap. Small
bug fixes, documentation improvements, and focused tests can go straight to a pull request.

Never include real API tokens, private brew names, comments, telemetry, inventory, or account data in
issues, fixtures, screenshots, or commits.

## Development setup

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/MrFresskopf/forge-companion.git
cd forge-companion
uv sync --extra dev
```

Run the CLI locally:

```bash
uv run forge-companion --help
```

## Make a change

1. Create a focused branch.
2. Add or update tests for behavior changes.
3. Keep API use explicit and rate-limit aware.
4. Preserve concise errors without tracebacks or secret values.
5. Update the README, command guide, or roadmap when behavior changes.

Write operations are outside the current default scope. A proposal involving writes or hardware must
start with a separate design covering opt-in controls, dry runs, idempotency, timeouts, read-back
verification, and a fail-closed path.

## Check your work

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

All three checks must pass. Generated reports should remain self-contained and must not load remote
resources or expose credentials and local paths.

## Pull requests

Keep the title specific and the diff small enough to review. Explain:

- what changes for the user
- how you tested it
- how many BrewForge requests the new path uses
- any privacy or safety implications

By contributing, you agree that your work is licensed under the repository's MIT license.
