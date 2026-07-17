"""Command-line interface for Forge Companion."""

import json
import os
from datetime import date
from pathlib import Path
from typing import Annotated

import httpx
import typer

from forge_companion.backup import create_backup, write_backup
from forge_companion.client import BrewForgeClient
from forge_companion.diagnostics import run_doctor
from forge_companion.inventory_audit import audit_inventory

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


@app.command("inventory-audit")
def inventory_audit_command(
    snapshot: Annotated[Path, typer.Argument(help="Collection snapshot JSON file.")],
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Audit date in YYYY-MM-DD format."),
    ] = None,
) -> None:
    """Audit inventory data from a local collection snapshot."""
    try:
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("snapshot root is not an object")
        if payload.get("format") != "forge-companion-collection-snapshot-v1":
            raise ValueError("unsupported snapshot format")
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise TypeError("snapshot resources is not an object")
        audit_date = date.fromisoformat(as_of) if as_of is not None else date.today()
        findings = audit_inventory(resources, as_of=audit_date)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as error:
        typer.echo(f"Inventory audit failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"{len(findings)} finding(s)")
    for finding in findings:
        typer.echo(
            f"{finding.severity.value.upper()} {finding.category} "
            f"{finding.name}: {finding.message}"
        )
