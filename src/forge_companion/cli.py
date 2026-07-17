"""Command-line interface for Forge Companion."""

import os
from pathlib import Path
from typing import Annotated

import httpx
import typer

from forge_companion.backup import create_backup, write_backup
from forge_companion.client import BrewForgeClient
from forge_companion.diagnostics import run_doctor

app = typer.Typer(
    help="Unofficial, read-only community tools for BrewForge.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Run read-only BrewForge companion commands."""


def _token_from_environment() -> str:
    token = os.getenv("BREWFORGE_API_TOKEN", "").strip()
    if not token:
        typer.echo("Error: BREWFORGE_API_TOKEN is not set.", err=True)
        raise typer.Exit(code=2)
    return token


@app.command()
def doctor() -> None:
    """Check authentication and documented read-only API collections."""
    client = BrewForgeClient(token=_token_from_environment())
    checks = run_doctor(client)
    for check in checks:
        marker = "OK" if check.ok else "FAIL"
        detail = str(check.status) if check.status is not None else check.error or "unknown error"
        typer.echo(f"{marker:4} {check.path:28} {detail}")
    if any(not check.ok for check in checks):
        raise typer.Exit(code=1)


@app.command("snapshot")
def snapshot_command(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination JSON file."),
    ] = Path("snapshots/brewforge-collections.json"),
) -> None:
    """Create a local snapshot of supported BrewForge API collections."""
    client = BrewForgeClient(token=_token_from_environment())
    try:
        payload = create_backup(client)
        write_backup(payload, output)
    except (httpx.HTTPError, OSError, TypeError, ValueError) as error:
        typer.echo(f"Snapshot failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Collection snapshot written to {output}")
