"""Typer commands for BrewForge reports, exports, and brew selection."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
import typer

from forge_companion import (
    preferences,
)
from forge_companion.cli_brewforge import _token_for_api
from forge_companion.cli_common import is_interactive_terminal as _is_interactive_terminal
from forge_companion.client import BrewForgeClient
from forge_companion.fermentation import analyze_readings, parse_readings
from forge_companion.fermentation_csv import render_csv, write_csv
from forge_companion.fermentation_html import render_html, write_html
from forge_companion.fermentation_report import render_markdown, write_markdown
from forge_companion.spunding_advisor import AdvisorConfig, advise_spunding_payload
from forge_companion.spunding_report import render_spunding_advice
from forge_companion.terminal_text import safe_terminal_text


def fermentation_brief_command(
    brew_id: Annotated[
        str | None,
        typer.Argument(help="Exact BrewForge brew UUID; omit when using --select."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Destination Markdown file."),
    ] = None,
    temperature_unit: Annotated[
        str | None,
        typer.Option("--temperature-unit", help="Explicit C or F; omitted means raw API value."),
    ] = None,
    select: Annotated[
        bool,
        typer.Option("--select", help="Choose a brew; each n or p requests one API page."),
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
    """Create a read-only Markdown brief for one pinned brew."""
    try:
        canonical_id = _selection_mode_brew_id(brew_id, select=select, page=page, limit=limit)
        unit = temperature_unit.upper() if temperature_unit is not None else None
        if unit not in {None, "C", "F"}:
            raise ValueError("temperature unit must be C or F")
        client = BrewForgeClient(token=_token_for_api())
        if select:
            selected_choice = _select_brew(client, page=page, limit=limit)
            canonical_id = selected_choice.id
            brew_name = selected_choice.report_name
        else:
            brew = client.get(f"brews/{canonical_id}")
            if brew.get("id") != canonical_id:
                raise ValueError("brew response ID does not match requested brew")
            raw_brew_name = brew.get("name")
            if not isinstance(raw_brew_name, str) or not raw_brew_name.strip():
                raise TypeError("brew response has no valid name")
            brew_name = raw_brew_name
        if canonical_id is None:
            raise ValueError("brew selection did not produce an ID")
        destination = output or Path("reports") / f"fermentation-{canonical_id}.md"
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


def fermentation_csv_command(
    brew_id: Annotated[
        str | None,
        typer.Argument(help="Exact BrewForge brew UUID; omit when using --select."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Destination CSV file."),
    ] = None,
    select: Annotated[
        bool,
        typer.Option("--select", help="Choose a brew; each n or p requests one API page."),
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
    """Export validated readings for one pinned brew as CSV."""
    try:
        canonical_id = _selection_mode_brew_id(brew_id, select=select, page=page, limit=limit)
        client = BrewForgeClient(token=_token_for_api())
        if select:
            canonical_id = _select_brew(client, page=page, limit=limit).id
        if canonical_id is None:
            raise ValueError("brew selection did not produce an ID")
        destination = output or Path("reports") / f"fermentation-{canonical_id}.csv"
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


class _BrewSelectionCancelled(ValueError):
    """Raised when a user deliberately leaves interactive brew selection."""


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


def _select_brew(client: BrewForgeClient, *, page: int, limit: int) -> _BrewChoice:
    current_page = page
    while True:
        payload = client.get("brews", params={"page": current_page, "limit": limit})
        choices, has_more = _validated_brew_choices(
            payload,
            page=current_page,
            limit=limit,
        )
        if not choices:
            raise ValueError(f"No brews found on page {current_page}.")
        for index, choice in enumerate(choices, start=1):
            typer.echo(f"{index}  {choice.terminal_name}")
        if has_more:
            typer.echo(f"More brews available: rerun with --select --page {current_page + 1}.")
            typer.echo("Enter n to load the next page.")
        if current_page > 1:
            typer.echo("Enter p for the previous page; enter q to cancel.")
        else:
            typer.echo("Enter q to cancel.")

        while True:
            response = str(typer.prompt("Brew number")).strip().lower()
            if response == "n":
                if not has_more:
                    typer.echo("brew selection has no next page", err=True)
                    continue
                current_page += 1
                break
            if response == "p":
                if current_page <= 1:
                    typer.echo("brew selection has no previous page", err=True)
                    continue
                current_page -= 1
                break
            if response == "q":
                raise _BrewSelectionCancelled("brew selection cancelled")
            try:
                selected_number = int(response)
            except ValueError:
                typer.echo("brew selection must be a number, n, p, or q", err=True)
                continue
            if not 1 <= selected_number <= len(choices):
                typer.echo(f"brew number must be between 1 and {len(choices)}", err=True)
                continue
            return choices[selected_number - 1]


def _selection_mode_brew_id(
    brew_id: str | None, *, select: bool, page: int, limit: int
) -> str | None:
    if brew_id is None and not select:
        raise ValueError("provide a brew UUID or --select")
    if brew_id is not None and select:
        raise ValueError("brew UUID and --select cannot be used together")
    if not select and (page != 1 or limit != 100):
        raise ValueError("--page and --limit require --select")
    return None if select else str(UUID(str(brew_id)))


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
        typer.Option("--select", help="Choose a brew; each n or p requests one API page."),
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
        canonical_id = _selection_mode_brew_id(brew_id, select=select, page=page, limit=limit)
        unit = temperature_unit.upper() if temperature_unit is not None else None
        if unit not in {None, "C", "F"}:
            raise ValueError("temperature unit must be C or F")
        client = BrewForgeClient(token=_token_for_api())
        selected_name: str | None = None
        if select:
            selected_choice = _select_brew(client, page=page, limit=limit)
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


def report_command(
    brew_id: Annotated[
        str | None,
        typer.Argument(help="Exact BrewForge brew UUID; omit to choose interactively."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Destination standalone HTML file."),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option("--title", help="Explicit report title."),
    ] = None,
    temperature_unit: Annotated[
        str | None,
        typer.Option(
            "--temperature-unit",
            help="Label API values as C or F; omitted uses the saved default or raw values.",
        ),
    ] = None,
    remember: Annotated[
        bool,
        typer.Option("--remember", help="Save the explicit temperature unit as the default."),
    ] = False,
) -> None:
    """Create the standard visual report, choosing a brew when needed."""
    if brew_id is None and not _is_interactive_terminal():
        typer.echo(
            "Report failed: automatic brew selection requires an interactive terminal; "
            "pass an exact brew UUID for scripts and pipelines. "
            "Run `forge-companion brews` to list UUIDs.",
            err=True,
        )
        raise typer.Exit(code=1)
    canonical_brew_id: str | None = None
    if brew_id is not None:
        try:
            canonical_brew_id = str(UUID(brew_id))
        except ValueError:
            typer.echo("Report failed: brew ID must be an exact UUID.", err=True)
            raise typer.Exit(code=1) from None
    explicit_unit = temperature_unit.upper() if temperature_unit is not None else None
    if explicit_unit not in {None, "C", "F"}:
        typer.echo("Report failed: temperature unit must be C or F.", err=True)
        raise typer.Exit(code=1)
    if remember and explicit_unit is None:
        typer.echo("Report failed: --remember requires --temperature-unit C or F.", err=True)
        raise typer.Exit(code=1)
    configured_unit: str | None = None
    if explicit_unit is None:
        try:
            configured_unit = preferences.load_preferences().temperature_unit
        except preferences.PreferencesError:
            typer.echo(
                "Report failed: local preferences are invalid or unreadable; "
                "override them with --temperature-unit C or F.",
                err=True,
            )
            raise typer.Exit(code=1) from None
    effective_unit = explicit_unit or configured_unit
    selected_id = canonical_brew_id
    selected_title = title
    if selected_id is None:
        try:
            client = BrewForgeClient(token=_token_for_api())
            selected_choice = _select_brew(client, page=1, limit=25)
        except _BrewSelectionCancelled:
            typer.echo("Report cancelled.")
            raise typer.Exit(code=1) from None
        except httpx.HTTPError:
            typer.echo("Report failed: API request failed.", err=True)
            raise typer.Exit(code=1) from None
        except (TypeError, ValueError) as error:
            typer.echo(f"Report failed: {error}", err=True)
            raise typer.Exit(code=1) from None
        selected_id = selected_choice.id
        if selected_title is None:
            selected_title = selected_choice.report_name
    fermentation_html_command(
        brew_id=selected_id,
        output=output,
        title=selected_title,
        temperature_unit=effective_unit,
        select=False,
        page=1,
        limit=100,
    )
    if remember and explicit_unit is not None:
        try:
            stored = preferences.load_preferences()
            preferences.save_preferences(replace(stored, temperature_unit=explicit_unit))
        except (OSError, preferences.PreferencesError):
            typer.echo(
                "Warning: report was written, but the preference could not be saved.",
                err=True,
            )
        else:
            typer.echo(f"Temperature unit {explicit_unit} saved as the report default.")


def spunding_advisor_command(
    trigger_sg: Annotated[
        float,
        typer.Option("--trigger-sg", help="Explicit SG threshold for this simulation."),
    ],
    brew_id: Annotated[
        str | None,
        typer.Argument(help="Exact BrewForge brew UUID; omit when using --select."),
    ] = None,
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
    select: Annotated[
        bool,
        typer.Option("--select", help="Choose a brew; each n or p requests one API page."),
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
    """Simulate one fail-closed spunding threshold evaluation."""
    try:
        canonical_id = _selection_mode_brew_id(brew_id, select=select, page=page, limit=limit)
        config = AdvisorConfig(
            trigger_sg=trigger_sg,
            max_age=timedelta(minutes=max_age_minutes),
            max_gap=timedelta(minutes=max_gap_minutes),
            confirmations=confirmations,
        )

        client = BrewForgeClient(token=_token_for_api())
        if select:
            canonical_id = _select_brew(client, page=page, limit=limit).id
        if canonical_id is None:
            raise ValueError("brew selection did not produce an ID")
        payload = client.get(f"brews/{canonical_id}/readings")
        result = advise_spunding_payload(payload, config=config, as_of=datetime.now(UTC))
        typer.echo(render_spunding_advice(result), nl=False)
    except httpx.HTTPError:
        typer.echo("Spunding advisor failed: API request failed.", err=True)
        raise typer.Exit(code=1) from None
    except (OverflowError, TypeError, ValueError) as error:
        typer.echo(f"Spunding advisor failed: {error}", err=True)
        raise typer.Exit(code=1) from None


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
        client = BrewForgeClient(token=_token_for_api())
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

def register_root_commands(root_app: typer.Typer) -> None:
    """Register report commands without changing their root command paths."""
    root_app.command("fermentation-brief", hidden=True)(fermentation_brief_command)
    root_app.command("fermentation-csv", hidden=True)(fermentation_csv_command)
    root_app.command("fermentation-html", hidden=True)(fermentation_html_command)
    root_app.command("report", rich_help_panel="Supporting BrewForge")(report_command)
    root_app.command("spunding-advisor", rich_help_panel="Safety experiments")(
        spunding_advisor_command
    )
    root_app.command("brews", hidden=True)(brews_command)
