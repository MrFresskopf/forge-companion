"""Command-line interface for Forge Companion."""

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
import typer

from forge_companion import __version__
from forge_companion.backup import create_backup, write_backup
from forge_companion.client import BrewForgeClient
from forge_companion.diagnostics import run_doctor
from forge_companion.fermentation import analyze_readings, parse_readings
from forge_companion.fermentation_csv import render_csv, write_csv
from forge_companion.fermentation_html import render_html, write_html
from forge_companion.fermentation_report import render_markdown, write_markdown
from forge_companion.inventory_audit import audit_inventory
from forge_companion.spunding_advisor import AdvisorConfig, advise_spunding_payload
from forge_companion.spunding_report import render_spunding_advice
from forge_companion.terminal_text import safe_terminal_text

app = typer.Typer(
    help="Unofficial, read-only community tools for BrewForge.",
    no_args_is_help=True,
    invoke_without_command=True,
)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the version and exit.", is_eager=True),
    ] = False,
) -> None:
    """Run read-only BrewForge companion commands."""
    if version:
        typer.echo(f"Forge Companion {__version__}")
        raise typer.Exit()


def _token_from_environment() -> str:
    token = os.getenv("BREWFORGE_API_TOKEN", "").strip()
    if not token:
        typer.echo("Error: BREWFORGE_API_TOKEN is not set.", err=True)
        raise typer.Exit(code=2)
    return token


@app.command(rich_help_panel="Start here")
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


@app.command("snapshot", rich_help_panel="Protect and inspect")
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
    except httpx.HTTPError:
        typer.echo("Snapshot failed: API request failed.", err=True)
        raise typer.Exit(code=1) from None
    except (OSError, TypeError, ValueError) as error:
        typer.echo(f"Snapshot failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Collection snapshot written to {output}")


@app.command("inventory-audit", rich_help_panel="Protect and inspect")
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
            f"{finding.severity.value.upper()} {finding.category} {finding.name}: {finding.message}"
        )


@app.command("fermentation-brief", rich_help_panel="Reports and exports")
def fermentation_brief_command(
    brew_id: Annotated[str, typer.Argument(help="Exact BrewForge brew UUID.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Destination Markdown file."),
    ] = None,
    temperature_unit: Annotated[
        str | None,
        typer.Option("--temperature-unit", help="Explicit C or F; omitted means raw API value."),
    ] = None,
) -> None:
    """Create a read-only Markdown brief for one pinned brew."""
    try:
        canonical_id = str(UUID(brew_id))
        unit = temperature_unit.upper() if temperature_unit is not None else None
        if unit not in {None, "C", "F"}:
            raise ValueError("temperature unit must be C or F")
        destination = output or Path("reports") / f"fermentation-{canonical_id}.md"
        client = BrewForgeClient(token=_token_from_environment())
        brew = client.get(f"brews/{canonical_id}")
        if brew.get("id") != canonical_id:
            raise ValueError("brew response ID does not match requested brew")
        brew_name = brew.get("name")
        if not isinstance(brew_name, str) or not brew_name.strip():
            raise TypeError("brew response has no valid name")
        readings_payload = client.get(f"brews/{canonical_id}/readings")
        parsed = parse_readings(readings_payload)
        report_time = datetime.now(UTC)
        metrics = analyze_readings(parsed, report_time=report_time)
        report = render_markdown(
            brew_name=brew_name,
            brew_id=canonical_id,
            parsed=parsed,
            metrics=metrics,
            report_time=report_time,
            temperature_unit=unit,
        )
        write_markdown(report, destination)
    except httpx.HTTPError:
        typer.echo("Fermentation brief failed: API request failed.", err=True)
        raise typer.Exit(code=1) from None
    except (OSError, TypeError, ValueError) as error:
        typer.echo(f"Fermentation brief failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Fermentation brief written to {destination}")


@app.command("fermentation-csv", rich_help_panel="Reports and exports")
def fermentation_csv_command(
    brew_id: Annotated[str, typer.Argument(help="Exact BrewForge brew UUID.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Destination CSV file."),
    ] = None,
) -> None:
    """Export validated readings for one pinned brew as CSV."""
    try:
        canonical_id = str(UUID(brew_id))
        destination = output or Path("reports") / f"fermentation-{canonical_id}.csv"
        client = BrewForgeClient(token=_token_from_environment())
        payload = client.get(f"brews/{canonical_id}/readings")
        parsed = parse_readings(payload)
        if not parsed.readings:
            raise ValueError("no valid fermentation readings")
        write_csv(render_csv(parsed), destination)
    except httpx.HTTPError:
        typer.echo("Fermentation CSV failed: API request failed.", err=True)
        raise typer.Exit(code=1) from None
    except OSError:
        typer.echo("Fermentation CSV failed: local file operation failed.", err=True)
        raise typer.Exit(code=1) from None
    except (TypeError, ValueError) as error:
        typer.echo(f"Fermentation CSV failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    safe_destination = safe_terminal_text(str(destination), limit=300)
    typer.echo(
        f"{len(parsed.readings)} readings written to {safe_destination} "
        f"({len(parsed.rejected)} rejected; "
        f"{len(parsed.conflicting_timestamps)} conflicting timestamps)"
    )


@dataclass(frozen=True)
class _BrewChoice:
    id: str
    terminal_name: str
    report_name: str


def _validated_brew_choices(
    payload: dict[str, object], *, page: int, limit: int
) -> tuple[list[_BrewChoice], bool]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise TypeError("brews response has no list-shaped data field")
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        raise TypeError("brews response has no object-shaped pagination")
    has_more = pagination.get("hasMore")
    if not isinstance(has_more, bool):
        raise TypeError("pagination.hasMore must be a boolean")
    total = pagination.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise TypeError("pagination.total must be a non-negative integer")
    if has_more and not data:
        raise ValueError("pagination made no progress while hasMore is true")
    returned_end = (page - 1) * limit + len(data)
    pagination_contradiction = (
        len(data) > limit
        or (bool(data) and returned_end > total)
        or (has_more and returned_end >= total)
        or (not has_more and returned_end < total)
    )
    if pagination_contradiction:
        raise ValueError("pagination metadata contradicts returned data")

    choices: list[_BrewChoice] = []
    for item in data:
        if not isinstance(item, dict):
            raise TypeError("brew is not an object")
        raw_id = item.get("id")
        if not isinstance(raw_id, str):
            raise TypeError("brew ID is not a string")
        choice_id = str(UUID(raw_id))
        if "name" not in item or not str(item.get("name", "")).strip():
            report_name = "<unnamed brew>"
            terminal_name = report_name
        else:
            name = item["name"]
            if not isinstance(name, str):
                raise TypeError("brew name is not a string")
            report_name = name.strip()
            terminal_name = safe_terminal_text(report_name)
            if not terminal_name:
                raise ValueError("brew name is empty after terminal sanitization")
        choices.append(_BrewChoice(choice_id, terminal_name, report_name))
    return choices, has_more


@app.command("fermentation-html", rich_help_panel="Reports and exports")
def fermentation_html_command(
    brew_id: Annotated[
        str | None,
        typer.Argument(help="Exact BrewForge brew UUID; omit when using --select."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Destination standalone HTML file."),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option("--title", help="Explicit report title; no brew detail request is made."),
    ] = None,
    temperature_unit: Annotated[
        str | None,
        typer.Option("--temperature-unit", help="Explicit C or F; omitted means raw API value."),
    ] = None,
    select: Annotated[
        bool,
        typer.Option("--select", help="Choose a brew interactively from one API page."),
    ] = False,
    page: Annotated[
        int,
        typer.Option("--page", min=1, help="One-indexed brew page used with --select."),
    ] = 1,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=100, help="Brews shown with --select."),
    ] = 100,
) -> None:
    """Create a self-contained HTML report for one pinned brew."""
    try:
        if brew_id is None and not select:
            raise ValueError("provide a brew UUID or --select")
        if brew_id is not None and select:
            raise ValueError("brew UUID and --select cannot be used together")
        if not select and (page != 1 or limit != 100):
            raise ValueError("--page and --limit require --select")
        unit = temperature_unit.upper() if temperature_unit is not None else None
        if unit not in {None, "C", "F"}:
            raise ValueError("temperature unit must be C or F")
        canonical_id = None if select else str(UUID(str(brew_id)))
        client = BrewForgeClient(token=_token_from_environment())
        selected_name: str | None = None
        if select:
            brew_payload = client.get("brews", params={"page": page, "limit": limit})
            choices, has_more = _validated_brew_choices(brew_payload, page=page, limit=limit)
            if not choices:
                raise ValueError(f"No brews found on page {page}.")
            for index, choice in enumerate(choices, start=1):
                typer.echo(f"{index}  {choice.terminal_name}")
            if has_more:
                typer.echo(f"More brews available: rerun with --select --page {page + 1}.")
            selected_number = typer.prompt("Brew number", type=int)
            if not 1 <= selected_number <= len(choices):
                raise ValueError(f"brew number must be between 1 and {len(choices)}")
            selected_choice = choices[selected_number - 1]
            canonical_id = selected_choice.id
            selected_name = selected_choice.report_name
        if canonical_id is None:
            raise ValueError("brew selection did not produce an ID")
        destination = output or Path("reports") / f"fermentation-{canonical_id}.html"
        report_title = title if title is not None else selected_name or f"Brew {canonical_id}"
        payload = client.get(f"brews/{canonical_id}/readings")
        parsed = parse_readings(payload)
        report_time = datetime.now(UTC)
        metrics = analyze_readings(parsed, report_time=report_time)
        report = render_html(
            title=report_title,
            brew_id=canonical_id,
            parsed=parsed,
            metrics=metrics,
            report_time=report_time,
            temperature_unit=unit,
        )
        write_html(report, destination)
    except httpx.HTTPError:
        typer.echo("Fermentation HTML failed: API request failed.", err=True)
        raise typer.Exit(code=1) from None
    except OSError:
        typer.echo("Fermentation HTML failed: local file operation failed.", err=True)
        raise typer.Exit(code=1) from None
    except (TypeError, ValueError) as error:
        typer.echo(f"Fermentation HTML failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    safe_destination = safe_terminal_text(str(destination), limit=300)
    typer.echo(
        f"{len(parsed.readings)} readings written to {safe_destination} "
        f"({len(parsed.rejected)} rejected; "
        f"{len(parsed.conflicting_timestamps)} conflicting timestamps)"
    )


@app.command("spunding-advisor", rich_help_panel="Safety experiments")
def spunding_advisor_command(
    brew_id: Annotated[str, typer.Argument(help="Exact BrewForge brew UUID.")],
    trigger_sg: Annotated[
        float,
        typer.Option("--trigger-sg", help="Explicit SG threshold for this simulation."),
    ],
    max_age_minutes: Annotated[
        int,
        typer.Option("--max-age-minutes", help="Maximum age of the newest reading."),
    ] = 90,
    max_gap_minutes: Annotated[
        int,
        typer.Option("--max-gap-minutes", help="Maximum gap between confirmation readings."),
    ] = 120,
    confirmations: Annotated[
        int,
        typer.Option("--confirmations", help="Required latest readings at or below trigger SG."),
    ] = 2,
) -> None:
    """Simulate one fail-closed spunding threshold evaluation."""
    try:
        canonical_id = str(UUID(brew_id))
        config = AdvisorConfig(
            trigger_sg=trigger_sg,
            max_age=timedelta(minutes=max_age_minutes),
            max_gap=timedelta(minutes=max_gap_minutes),
            confirmations=confirmations,
        )
        client = BrewForgeClient(token=_token_from_environment())
        payload = client.get(f"brews/{canonical_id}/readings")
        result = advise_spunding_payload(payload, config=config, as_of=datetime.now(UTC))
        typer.echo(render_spunding_advice(result), nl=False)
    except httpx.HTTPError:
        typer.echo("Spunding advisor failed: API request failed.", err=True)
        raise typer.Exit(code=1) from None
    except (OverflowError, TypeError, ValueError) as error:
        typer.echo(f"Spunding advisor failed: {error}", err=True)
        raise typer.Exit(code=1) from None


@app.command("brews", rich_help_panel="Start here")
def brews_command(
    page: Annotated[
        int,
        typer.Option("--page", min=1, help="One-indexed BrewForge page."),
    ] = 1,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=100, help="Brews to request, from 1 to 100."),
    ] = 100,
) -> None:
    """List brew names and UUIDs using one read-only request."""
    try:
        client = BrewForgeClient(token=_token_from_environment())
        payload = client.get("brews", params={"page": page, "limit": limit})
        choices, has_more = _validated_brew_choices(payload, page=page, limit=limit)
        if not choices:
            typer.echo(f"No brews found on page {page}.")
        else:
            for choice in choices:
                typer.echo(f"{choice.terminal_name} | {choice.id}")
        if has_more:
            typer.echo(f"More brews available: rerun with --page {page + 1}.")
    except httpx.HTTPError:
        typer.echo("Brew list failed: API request failed.", err=True)
        raise typer.Exit(code=1) from None
    except (TypeError, ValueError) as error:
        typer.echo(f"Brew list failed: {error}", err=True)
        raise typer.Exit(code=1) from None
