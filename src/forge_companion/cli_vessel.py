"""Experimental offline vessel associations and read-only online telemetry."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import typer

from forge_companion import rapt_credentials
from forge_companion.brewforge_brew import (
    BrewForgeBrewDetailError,
    context_source_from_brew_detail,
)
from forge_companion.brewforge_telemetry import (
    _canonical_brew_id,
    _canonical_reading_id,
    _measurement,
    adapt_brewforge_readings,
)
from forge_companion.cli_brewforge import _token_for_api
from forge_companion.cli_rapt import _profile_for_api, _utc_datetime
from forge_companion.cli_reports import (
    _BrewSelectionCancelled,
    _select_brew,
    _selection_mode_brew_id,
)
from forge_companion.client import BrewForgeClient
from forge_companion.fermentation_contexts import (
    FermentationContextBusyError,
    append_fermentation_phase,
    close_fermentation_context,
    context_binding_status,
    context_phase_history,
    load_fermentation_contexts,
    preflight_context_start,
    start_brewforge_fermentation_context,
    start_fermentation_context,
    validate_context_input_fields,
)
from forge_companion.rapt import RaptClient, RaptError
from forge_companion.rapt_sg import normalize_rapt_sg
from forge_companion.sg_trend import TrendSegment, _datetime_ns, build_trend_report, exact_ns
from forge_companion.spunding_advisor import TelemetrySgReason, explain_telemetry_sg
from forge_companion.telemetry import (
    DeviceKind,
    RaptTelemetrySource,
    TelemetryReading,
    TelemetryValidationError,
    _utc_timestamp,
)
from forge_companion.vessels import VesselBusyError, bind_vessel, load_vessels

vessel_app = typer.Typer(help="Experimental vessel bindings and read-only telemetry.")
context_app = typer.Typer(help="Experimental offline fermentation contexts.")
vessel_app.add_typer(context_app, name="context")

STATUS_WINDOW_HOURS = 48
DEFAULT_PILL_MAX_AGE_MINUTES = 90.0
DEFAULT_CONTROLLER_MAX_AGE_MINUTES = 30.0
MAX_FRESHNESS_MINUTES = 2880.0
MAX_TREND_WINDOW = timedelta(days=7)


@vessel_app.callback()
def vessel_command(context: typer.Context) -> None:
    """Manage offline bindings or read both devices online without controlling them."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


def _binding_line(binding: dict[str, str]) -> str:
    return " ".join(f"{key}={value}" for key, value in binding.items())


@vessel_app.command("bind")
def vessel_bind(
    vessel_id: str,
    hydrometer: Annotated[str, typer.Option("--hydrometer")],
    temperature_controller: Annotated[str, typer.Option("--temperature-controller")],
    gravity_interpretation: Annotated[str, typer.Option("--gravity-interpretation")] = "unknown",
    replace: Annotated[bool, typer.Option("--replace")] = False,
) -> None:
    """Bind both devices locally; no credentials or remote requests."""
    try:
        bind_vessel(
            {
                "vessel_id": vessel_id,
                "hydrometer": hydrometer,
                "temperature_controller": temperature_controller,
                "gravity_interpretation": gravity_interpretation,
            },
            replace=replace,
        )
    except (ValueError, VesselBusyError, OSError):
        typer.echo("Vessel binding failed: invalid input, conflict, or local file.", err=True)
        raise typer.Exit(1) from None
    typer.echo("Vessel bound locally. No remote request was sent.")


@vessel_app.command("list")
def vessel_list() -> None:
    """List local bindings."""
    try:
        bindings = load_vessels()
    except (ValueError, OSError):
        typer.echo("Vessel listing failed: local file is invalid or unavailable.", err=True)
        raise typer.Exit(1) from None
    for binding in bindings:
        typer.echo(_binding_line(binding))


@vessel_app.command("show")
def vessel_show(vessel_id: str) -> None:
    """Show one local binding."""
    try:
        bindings = load_vessels()
    except (ValueError, OSError):
        typer.echo("Vessel lookup failed: local file is invalid or unavailable.", err=True)
        raise typer.Exit(1) from None
    for binding in bindings:
        if binding["vessel_id"] == vessel_id:
            typer.echo(_binding_line(binding))
            return
    typer.echo("Vessel not found.", err=True)
    raise typer.Exit(1)


def _context_line(item: dict[str, object], *, binding_status: str) -> str:
    sources = item["source_devices"]
    if not isinstance(sources, dict):
        raise ValueError("invalid source devices")
    phases = context_phase_history(item)
    latest = phases[-1]
    phase_text = ",".join(f"{event['phase']}@{event['start_date']}" for event in phases)
    fields = [
        f"context_id={item['context_id']}",
        f"vessel_id={item['vessel_id']}",
        f"status={item['status']}",
        f"batch={item['batch_display_name']}",
        f"brewforge_brew_id={item.get('brewforge_brew_id', 'unavailable')}",
        f"original_gravity_sg={item['original_gravity_sg']}",
        f"expected_final_gravity_sg={item['expected_final_gravity_sg']}",
        f"yeast={item['yeast']}",
        f"fermentation_start_date={item['fermentation_start_date']}",
        "start_instant=unavailable",
        "same_day_telemetry_attribution=unavailable",
        f"authoritative_temperature_role={item['authoritative_temperature_role']}",
        f"hydrometer={sources['hydrometer']}",
        f"temperature_controller={sources['temperature_controller']}",
        f"binding_status={binding_status}",
        f"latest_recorded_phase={latest['phase']}",
        f"phase_history={phase_text}",
    ]
    return " ".join(fields)


@context_app.command("start")
def context_start(
    vessel_id: str,
    batch: Annotated[str, typer.Option("--batch")],
    original_gravity_sg: Annotated[str, typer.Option("--original-gravity-sg")],
    expected_final_gravity_sg: Annotated[str, typer.Option("--expected-final-gravity-sg")],
    yeast: Annotated[str, typer.Option("--yeast")],
    start_date: Annotated[str, typer.Option("--start-date")],
    authoritative_temperature_role: Annotated[
        str, typer.Option("--authoritative-temperature-role")
    ],
    switch: Annotated[bool, typer.Option("--switch")] = False,
) -> None:
    """Start a local context; --switch explicitly closes the prior active context."""
    try:
        item = start_fermentation_context(
            vessel_id=vessel_id,
            batch_display_name=batch,
            original_gravity_sg=original_gravity_sg,
            expected_final_gravity_sg=expected_final_gravity_sg,
            yeast=yeast,
            fermentation_start_date=start_date,
            authoritative_temperature_role=authoritative_temperature_role,
            switch=switch,
        )
    except (FermentationContextBusyError, OSError, TypeError, ValueError):
        typer.echo("Context start failed: invalid input, conflict, or local file.", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Fermentation context started: {item['context_id']}")
    typer.echo("No credentials, API, telemetry, or device command was used.")


_CONTEXT_SOURCE_OVERRIDES = (
    ("batch_display_name", "--batch"),
    ("original_gravity_sg", "--original-gravity-sg"),
    ("expected_final_gravity_sg", "--expected-final-gravity-sg"),
    ("yeast", "--yeast"),
    ("fermentation_start_date", "--start-date"),
)


@context_app.command("start-brewforge")
def context_start_brewforge(
    vessel_id: str,
    authoritative_temperature_role: Annotated[
        str, typer.Option("--authoritative-temperature-role")
    ],
    brew_id: Annotated[
        str | None,
        typer.Argument(help="Exact BrewForge brew UUID; omit when using --select."),
    ] = None,
    batch: Annotated[
        str | None,
        typer.Option("--batch", help="Override the BrewForge brew name."),
    ] = None,
    original_gravity_sg: Annotated[
        str | None,
        typer.Option("--original-gravity-sg", help="Override measured.originalGravity."),
    ] = None,
    expected_final_gravity_sg: Annotated[
        str | None,
        typer.Option("--expected-final-gravity-sg", help="Override calculated.fg."),
    ] = None,
    yeast: Annotated[
        str | None,
        typer.Option("--yeast", help="Override recipe yeast names."),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Override an ambiguous brewDate."),
    ] = None,
    switch: Annotated[bool, typer.Option("--switch")] = False,
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
    """Start a local context from a read-only BrewForge brew detail lookup."""
    try:
        canonical_id = _selection_mode_brew_id(brew_id, select=select, page=page, limit=limit)
        if authoritative_temperature_role not in {"hydrometer", "controller"}:
            raise ValueError("authoritative temperature role must be hydrometer or controller")
        overrides = validate_context_input_fields(
            batch_display_name=batch,
            original_gravity_sg=original_gravity_sg,
            expected_final_gravity_sg=expected_final_gravity_sg,
            yeast=yeast,
            fermentation_start_date=start_date,
        )
        preflight_context_start(
            vessel_id,
            switch=switch,
            contexts=load_fermentation_contexts(),
            bindings=load_vessels(),
        )
    except OSError:
        typer.echo(
            "Context start from BrewForge failed: local file is invalid or unavailable.",
            err=True,
        )
        raise typer.Exit(1) from None
    except (TypeError, ValueError) as error:
        typer.echo(f"Context start from BrewForge failed: {error}", err=True)
        raise typer.Exit(1) from None

    client = BrewForgeClient(token=_token_for_api())
    try:
        if select:
            canonical_id = _select_brew(client, page=page, limit=limit).id
        if canonical_id is None:
            raise ValueError("brew selection did not produce an ID")
        detail = client.get(f"brews/{canonical_id}")
        source = context_source_from_brew_detail(detail, brew_id=canonical_id)
    except _BrewSelectionCancelled:
        typer.echo("Context start from BrewForge failed: brew selection cancelled.", err=True)
        raise typer.Exit(1) from None
    except httpx.HTTPError:
        typer.echo("Context start from BrewForge failed: API request failed.", err=True)
        raise typer.Exit(1) from None
    except BrewForgeBrewDetailError:
        typer.echo(
            "Context start from BrewForge failed: BrewForge brew detail is invalid or unsupported.",
            err=True,
        )
        raise typer.Exit(1) from None
    except (TypeError, ValueError):
        typer.echo(
            "Context start from BrewForge failed: brew selection or detail is invalid.",
            err=True,
        )
        raise typer.Exit(1) from None

    resolved: dict[str, str | None] = {
        "batch_display_name": overrides.get("batch_display_name", source.batch_display_name),
        "original_gravity_sg": overrides.get("original_gravity_sg", source.original_gravity_sg),
        "expected_final_gravity_sg": overrides.get(
            "expected_final_gravity_sg", source.expected_final_gravity_sg
        ),
        "yeast": overrides.get("yeast", source.yeast),
        "fermentation_start_date": overrides.get(
            "fermentation_start_date", source.fermentation_start_date
        ),
    }
    missing = [flag for field, flag in _CONTEXT_SOURCE_OVERRIDES if resolved[field] is None]
    if missing:
        typer.echo(
            "Context start from BrewForge failed: brew is missing required data; pass "
            + ", ".join(missing)
            + ".",
            err=True,
        )
        raise typer.Exit(1)

    try:
        item = start_brewforge_fermentation_context(
            vessel_id=vessel_id,
            brewforge_brew_id=canonical_id,
            batch_display_name=resolved["batch_display_name"] or "",
            original_gravity_sg=resolved["original_gravity_sg"] or "",
            expected_final_gravity_sg=resolved["expected_final_gravity_sg"] or "",
            yeast=resolved["yeast"] or "",
            fermentation_start_date=resolved["fermentation_start_date"] or "",
            authoritative_temperature_role=authoritative_temperature_role,
            switch=switch,
        )
    except (FermentationContextBusyError, OSError, TypeError, ValueError):
        typer.echo(
            "Context start from BrewForge failed: invalid input, conflict, or local file.",
            err=True,
        )
        raise typer.Exit(1) from None
    typer.echo(f"Fermentation context started from BrewForge: {item['context_id']}")
    typer.echo("expected_final_gravity_sg is the brew's calculated estimate, not a measurement.")
    typer.echo(
        "Read-only BrewForge lookup; no BrewForge, RAPT, or Shelly write and no device "
        "command was sent."
    )


@context_app.command("show")
def context_show(
    vessel_id: str,
    show_all: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    """Show the active context, or retained history with --all."""
    try:
        contexts = [item for item in load_fermentation_contexts() if item["vessel_id"] == vessel_id]
        if not show_all:
            contexts = [item for item in contexts if item["status"] == "active"]
        if not contexts:
            raise ValueError("not found")
        bindings = load_vessels()
        lines = [
            _context_line(item, binding_status=context_binding_status(item, bindings))
            for item in contexts
        ]
    except (OSError, TypeError, ValueError):
        typer.echo("Context lookup failed: not found or local file is invalid.", err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)


@context_app.command("phase")
def context_phase(
    vessel_id: str,
    phase: Annotated[str, typer.Option("--phase")],
    start_date: Annotated[str, typer.Option("--start-date")],
) -> None:
    """Append a recorded date-only phase transition to the active context."""
    try:
        item = append_fermentation_phase(vessel_id=vessel_id, phase=phase, start_date=start_date)
    except (FermentationContextBusyError, OSError, TypeError, ValueError):
        typer.echo(
            "Context phase failed: invalid transition, no active context, or local file.",
            err=True,
        )
        raise typer.Exit(1) from None
    typer.echo(f"Fermentation phase recorded: {item['context_id']} {phase}@{start_date}")
    typer.echo("No credentials, API, telemetry, or device command was used.")


@context_app.command("close")
def context_close(vessel_id: str) -> None:
    """Explicitly close and retain a vessel's active context."""
    try:
        item = close_fermentation_context(vessel_id)
    except (FermentationContextBusyError, OSError, TypeError, ValueError):
        typer.echo("Context close failed: no active context or local file is invalid.", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Fermentation context closed and retained: {item['context_id']}")
    typer.echo("No credentials, API, telemetry, or device command was used.")


def _read_vessel_streams(
    *,
    profile: rapt_credentials.RaptProfile,
    binding: dict[str, str],
    start: datetime,
    end: datetime,
) -> tuple[tuple[TelemetryReading, ...], tuple[TelemetryReading, ...]]:
    with RaptClient(username=profile.username, api_secret=profile.api_secret) as client:
        hydrometer = RaptTelemetrySource(client=client, kind=DeviceKind.HYDROMETER).get_readings(
            device_id=binding["hydrometer"], start=start, end=end
        )
        controller = RaptTelemetrySource(
            client=client, kind=DeviceKind.TEMPERATURE_CONTROLLER
        ).get_readings(device_id=binding["temperature_controller"], start=start, end=end)
    return hydrometer, controller


def _read_hydrometer(
    *,
    profile: rapt_credentials.RaptProfile,
    device_id: str,
    start: datetime,
    end: datetime,
) -> tuple[TelemetryReading, ...]:
    with RaptClient(username=profile.username, api_secret=profile.api_secret) as client:
        return RaptTelemetrySource(client=client, kind=DeviceKind.HYDROMETER).get_readings(
            device_id=device_id, start=start, end=end
        )


def _vessel_reading_line(reading: TelemetryReading, *, gravity_interpretation: str) -> str:
    fields = [reading.observed_at_exact]
    if reading.temperature_c is not None:
        fields.append(f"temperature={reading.temperature_c:.1f} C")
    if reading.gravity_raw is not None:
        fields.append(f"gravity_raw={reading.gravity_raw:.4f}")
        if gravity_interpretation == "sg-times-1000":
            fields.append(f"sg={reading.gravity_raw / 1000:.4f}")
    if reading.gravity_velocity_raw is not None:
        fields.append(f"gravity_velocity_raw={reading.gravity_velocity_raw:.6f}")
    if reading.target_temperature_c is not None:
        fields.append(f"target={reading.target_temperature_c:.1f} C")
    if reading.control_temperature_c is not None:
        fields.append(f"control={reading.control_temperature_c:.1f} C")
    if reading.battery_percent is not None:
        fields.append(f"battery={reading.battery_percent:.1f}%")
    if reading.rssi is not None:
        fields.append(f"rssi={reading.rssi:.1f}")
    return " ".join(fields)


def _utc_now() -> datetime:
    """Capture the status boundary through one injectable timezone-aware UTC clock."""
    return datetime.now(UTC)


def _validated_max_age(value: float) -> int:
    """Return an exact integer nanosecond policy threshold."""
    if not isfinite(value) or value <= 0 or value > MAX_FRESHNESS_MINUTES:
        raise ValueError("invalid freshness threshold")
    return int(Decimal(str(value)) * Decimal(60_000_000_000))


def _age_ns(reading: TelemetryReading, *, now: datetime) -> int:
    elapsed = now - reading.observed_at
    return (
        (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000_000
        + elapsed.microseconds * 1_000
        - reading.observed_at_submicrosecond_ns
    )


def _threshold_minutes_label(threshold_ns: int) -> str:
    minutes = Decimal(threshold_ns) / Decimal(60_000_000_000)
    text = format(minutes, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _approximate_age_minutes(age_ns: int) -> str:
    minutes = Decimal(age_ns) / Decimal(60_000_000_000)
    return format(minutes, ".6f").rstrip("0").rstrip(".")


def _freshness_status(age_ns: int, maximum_age_ns: int) -> str:
    # Sources reject observations beyond the captured exact window.
    if age_ns < 0:
        raise TelemetryValidationError("RAPT telemetry lies outside the requested window")
    return "CURRENT" if age_ns <= maximum_age_ns else "STALE"


def _status_line(
    heading: str,
    readings: tuple[TelemetryReading, ...],
    *,
    now: datetime,
    maximum_age_ns: int,
) -> str:
    if not readings:
        return (
            f"{heading} status=NO_DATA latest=NO_OBSERVATION_IN_WINDOW age=NO_DATA "
            f"temperature=NO_DATA history=NOT_QUERIED "
            f"reason=no_observations_in_recent_{STATUS_WINDOW_HOURS}h"
        )
    latest = readings[-1]
    age_ns = _age_ns(latest, now=now)
    status = _freshness_status(age_ns, maximum_age_ns)
    temperature = (
        "temperature=NO_DATA"
        if latest.temperature_c is None
        else f"temperature={latest.temperature_c:.1f} C temperature_status={status}"
    )
    return (
        f"{heading} status={status} latest={latest.observed_at_exact} "
        f"age_ns={age_ns} age_approx={_approximate_age_minutes(age_ns)} minutes {temperature}"
    )


@vessel_app.command("status")
def vessel_status(
    vessel_id: str,
    pill_max_age_minutes: Annotated[
        float,
        typer.Option(
            "--pill-max-age-minutes",
            help="Provisional Pill warning threshold (0 < minutes <= 2880).",
        ),
    ] = DEFAULT_PILL_MAX_AGE_MINUTES,
    controller_max_age_minutes: Annotated[
        float,
        typer.Option(
            "--controller-max-age-minutes",
            help="Provisional controller warning threshold (0 < minutes <= 2880).",
        ),
    ] = DEFAULT_CONTROLLER_MAX_AGE_MINUTES,
) -> None:
    """Check recent bound-device freshness without writing or controlling anything."""
    try:
        pill_max_age_ns = _validated_max_age(pill_max_age_minutes)
        controller_max_age_ns = _validated_max_age(controller_max_age_minutes)
        bindings = load_vessels()
        binding = next(item for item in bindings if item["vessel_id"] == vessel_id)
    except (OSError, StopIteration, TypeError, ValueError):
        typer.echo("Vessel status failed: binding, local file, or threshold is invalid.", err=True)
        raise typer.Exit(1) from None

    profile = _profile_for_api()
    now = _utc_now()
    if now.tzinfo is None or now.utcoffset() is None:
        typer.echo("Vessel status failed: clock is invalid.", err=True)
        raise typer.Exit(1)
    now = now.astimezone(UTC)
    start = now - timedelta(hours=STATUS_WINDOW_HOURS)
    try:
        hydrometer, controller = _read_vessel_streams(
            profile=profile, binding=binding, start=start, end=now
        )
        lines = (
            _status_line(
                "HYDROMETER",
                hydrometer,
                now=now,
                maximum_age_ns=pill_max_age_ns,
            ),
            _status_line(
                "TEMPERATURE_CONTROLLER",
                controller,
                now=now,
                maximum_age_ns=controller_max_age_ns,
            ),
        )
    except (RaptError, TelemetryValidationError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Vessel status failed: request or response is invalid.", err=True)
        raise typer.Exit(1) from None

    typer.echo("Vessel status read-only.")
    typer.echo(f"Vessel: {binding['vessel_id']}")
    typer.echo(f"Window: {start.isoformat()} / {now.isoformat()}")
    typer.echo(
        "Provisional warning thresholds: "
        f"Pill threshold_ns={pill_max_age_ns} "
        f"(~{_threshold_minutes_label(pill_max_age_ns)} minutes); "
        f"controller threshold_ns={controller_max_age_ns} "
        f"(~{_threshold_minutes_label(controller_max_age_ns)} minutes)."
    )
    for line in lines:
        typer.echo(line)
    typer.echo("Freshness is warning policy only; it grants no actuator permission.")
    typer.echo("No RAPT or Shelly device command was sent.")


@vessel_app.command("overview")
def vessel_overview(vessel_id: str) -> None:
    """Experimental read-only active context and recent device observations."""
    try:
        bindings = load_vessels()
        item = next(
            item for item in load_fermentation_contexts()
            if item["vessel_id"] == vessel_id and item["status"] == "active"
        )
        if context_binding_status(item, bindings) != "current":
            raise ValueError("context binding is not current")
    except (OSError, StopIteration, TypeError, ValueError):
        typer.echo(
            "Vessel overview failed: active context or matching binding unavailable.",
            err=True,
        )
        raise typer.Exit(1) from None

    binding = next(binding for binding in bindings if binding["vessel_id"] == vessel_id)
    profile = _profile_for_api()
    try:
        now = _utc_now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("invalid clock")
        now = now.astimezone(UTC)
        start = now - timedelta(hours=STATUS_WINDOW_HOURS)
        hydrometer, controller = _read_vessel_streams(
            profile=profile, binding=binding, start=start, end=now
        )
        latest_phase = context_phase_history(item)[-1]
        role = item["authoritative_temperature_role"]
        authoritative = hydrometer if role == "hydrometer" else controller
        temperature = authoritative[-1].temperature_c if authoritative else None
        gravity = hydrometer[-1].gravity_raw if hydrometer else None
        gravity_line = "PILL gravity_raw=NO_DATA sg=NO_DATA"
        if gravity is not None:
            if binding["gravity_interpretation"] == "sg-times-1000":
                sg = f"{gravity / 1000:.4f}"
            else:
                sg = "UNINTERPRETED"
            gravity_line = f"PILL gravity_raw={gravity:.4f} sg={sg}"
        pill_threshold = _validated_max_age(DEFAULT_PILL_MAX_AGE_MINUTES)
        controller_threshold = _validated_max_age(DEFAULT_CONTROLLER_MAX_AGE_MINUTES)
        temperature_status = "NO_DATA"
        if temperature is not None:
            temperature_status = _freshness_status(
                _age_ns(authoritative[-1], now=now),
                pill_threshold if role == "hydrometer" else controller_threshold,
            )
        lines = [
            "Vessel overview read-only.",
            f"Vessel: {vessel_id}",
            f"context_id={item['context_id']} batch={item['batch_display_name']} "
            f"yeast={item['yeast']}",
            f"original_gravity_sg={item['original_gravity_sg']} "
            f"expected_final_gravity_sg={item['expected_final_gravity_sg']}",
            f"latest_recorded_phase={latest_phase['phase']} "
            f"latest_recorded_phase_date={latest_phase['start_date']}",
            f"Window: {start.isoformat()} / {now.isoformat()}",
            "Provisional warning thresholds: "
            f"Pill threshold_ns={pill_threshold} "
            f"(~{_threshold_minutes_label(pill_threshold)} minutes); "
            f"controller threshold_ns={controller_threshold} "
            f"(~{_threshold_minutes_label(controller_threshold)} minutes).",
            _status_line("HYDROMETER", hydrometer, now=now, maximum_age_ns=pill_threshold),
            _status_line("TEMPERATURE_CONTROLLER", controller, now=now,
                         maximum_age_ns=controller_threshold),
            f"gravity_interpretation={binding['gravity_interpretation']} {gravity_line}",
            f"authoritative_temperature_role={role} authoritative_temperature="
            + ("NO_DATA" if temperature is None else f"{temperature:.1f} C")
            + f" authoritative_temperature_status={temperature_status}",
            "Freshness is warning policy only; it grants no actuator permission.",
            "No RAPT or Shelly device command was sent.",
        ]
    except (RaptError, TelemetryValidationError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Vessel overview failed: request or response is invalid.", err=True)
        raise typer.Exit(1) from None
    typer.echo("\n".join(lines))


@vessel_app.command("telemetry")
def vessel_telemetry(
    vessel_id: str,
    start: Annotated[str, typer.Option("--start", help="Inclusive timezone-aware start.")],
    end: Annotated[
        str,
        typer.Option("--end", help="Inclusive validation boundary for the timezone-aware end."),
    ],
) -> None:
    """Read both bound RAPT telemetry streams without controlling devices."""
    try:
        bindings = load_vessels()
        binding = next(item for item in bindings if item["vessel_id"] == vessel_id)
        start_at = _utc_datetime(start)
        end_at = _utc_datetime(end)
        if start_at >= end_at:
            raise ValueError("Invalid interval")
    except (OSError, StopIteration, TypeError, ValueError):
        typer.echo(
            "Vessel telemetry failed: binding, local file, or interval is invalid.",
            err=True,
        )
        raise typer.Exit(1) from None

    profile = _profile_for_api()
    try:
        hydrometer, controller = _read_vessel_streams(
            profile=profile, binding=binding, start=start_at, end=end_at
        )
    except (RaptError, TelemetryValidationError, httpx.HTTPError, OSError, TypeError):
        typer.echo("Vessel telemetry failed: request or response is invalid.", err=True)
        raise typer.Exit(1) from None

    typer.echo("Vessel telemetry read-only.")
    typer.echo(f"Vessel: {binding['vessel_id']}")
    typer.echo(f"Window: {start_at.isoformat()} / {end_at.isoformat()}")
    for heading, readings in (
        ("HYDROMETER", hydrometer),
        ("TEMPERATURE_CONTROLLER", controller),
    ):
        typer.echo(f"{heading} readings={len(readings)}")
        for reading in readings:
            typer.echo(
                _vessel_reading_line(
                    reading,
                    gravity_interpretation=binding["gravity_interpretation"],
                )
            )
    typer.echo("No RAPT or Shelly device command was sent.")


def _decimal_label(value: Decimal | None) -> str:
    return "NO_DATA" if value is None else format(value, "f")


def _trend_segment_line(segment: TrendSegment) -> str:
    return " ".join(
        (
            f"phase={segment.phase}",
            f"segment={segment.sequence}",
            f"observations={segment.observations}",
            f"missing_sg={segment.missing_sg}",
            f"first_at={segment.first_at or 'NO_DATA'}",
            f"latest_at={segment.latest_at or 'NO_DATA'}",
            f"first_sg={_decimal_label(segment.first_sg)}",
            f"latest_sg={_decimal_label(segment.latest_sg)}",
            f"delta_sg={_decimal_label(segment.delta_sg)}",
            "observed_duration_ns="
            f"{segment.duration_ns if segment.duration_ns is not None else 'NO_DATA'}",
            f"observed_rate_sg_per_day={_decimal_label(segment.rate_sg_per_day)}",
            f"max_gap_ns={segment.max_gap_ns if segment.max_gap_ns is not None else 'NO_DATA'}",
            f"gap_status={segment.gap_status}",
            f"coverage_at_query_end={segment.coverage_status}",
            f"sg_status={segment.sg_status}",
            f"endpoint_trend={segment.endpoint_trend}",
        )
    )


@vessel_app.command("sg-diagnose")
def vessel_sg_diagnose(
    vessel_id: str,
    start: Annotated[str, typer.Option("--start", help="Timezone-aware inclusive start.")],
    end: Annotated[str, typer.Option("--end", help="Inclusive end and evaluation time.")],
    trigger_sg: Annotated[str, typer.Option("--trigger-sg")],
    max_age_minutes: Annotated[float, typer.Option("--max-age-minutes")],
    max_gap_minutes: Annotated[float, typer.Option("--max-gap-minutes")],
    confirmations: Annotated[int, typer.Option("--confirmations", min=2, max=5)],
) -> None:
    """Experimental SG-only query-end diagnostics; never permission to act."""
    try:
        start_at = _utc_datetime(start)
        end_at = _utc_datetime(end)
        if start_at >= end_at or end_at - start_at > MAX_TREND_WINDOW:
            raise ValueError("invalid diagnostic interval")
        trigger = Decimal(trigger_sg)
        if not trigger.is_finite() or not Decimal("0.9") <= trigger <= Decimal("1.2"):
            raise ValueError("invalid trigger")
        max_age_ns = _validated_max_age(max_age_minutes)
        max_gap_ns = _validated_max_age(max_gap_minutes)
        if min(max_age_ns, max_gap_ns) <= 0:
            raise ValueError("limits must resolve to positive nanoseconds")
        bindings = load_vessels()
        binding = next(item for item in bindings if item["vessel_id"] == vessel_id)
        contexts = [
            item for item in load_fermentation_contexts()
            if item["vessel_id"] == vessel_id and item["status"] == "active"
        ]
        if len(contexts) != 1 or binding["gravity_interpretation"] != "sg-times-1000":
            raise ValueError("diagnostic precondition failed")
        if context_binding_status(contexts[0], bindings) != "current":
            raise ValueError("context binding drift")
    except (ArithmeticError, OSError, StopIteration, TypeError, ValueError):
        typer.echo("Vessel SG diagnostic failed: input or local state is invalid.", err=True)
        raise typer.Exit(1) from None

    profile = _profile_for_api()
    try:
        readings = normalize_rapt_sg(
            _read_hydrometer(
                profile=profile, device_id=binding["hydrometer"], start=start_at, end=end_at
            ),
            gravity_interpretation=binding["gravity_interpretation"],
        )
        elapsed = end_at - datetime(1970, 1, 1, tzinfo=UTC)
        now_ns = (
            (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000_000
            + elapsed.microseconds * 1_000
        )
        duration = end_at - start_at
        start_ns = now_ns - (
            (duration.days * 86_400 + duration.seconds) * 1_000_000_000
            + duration.microseconds * 1_000
        )
        if any(
            item.device_id != binding["hydrometer"]
            or not start_ns <= exact_ns(item) <= now_ns
            for item in readings
        ):
            raise ValueError("unexpected device or out-of-window reading")
        result = explain_telemetry_sg(
            readings, gravity_unit="sg", now_ns=now_ns, trigger_sg=trigger,
            max_age_ns=max_age_ns, max_gap_ns=max_gap_ns, confirmations=confirmations,
        )
        if (
            result.distinct_observations is None
            and result.reason != TelemetrySgReason.NO_READINGS
        ):
            raise ValueError("SG series integrity failure")
        lines = [
            "Vessel SG diagnostic read-only (experimental).",
            f"Window: {start_at.isoformat()} / {end_at.isoformat()}",
            f"evaluation_at_ns={now_ns} (query-end coverage, not live freshness)",
            "Stored sg-times-1000 is a caller assertion, not unit proof or verified calibration.",
            "SG-only window; no batch or phase attribution is inferred.",
            f"trigger_sg={trigger} max_age_ns={max_age_ns} max_gap_ns={max_gap_ns} "
            f"confirmations={confirmations}",
            f"status={result.status} reason={result.reason}",
            " ".join(
                f"{name}={value if value is not None else 'NO_DATA'}"
                for name, value in (
                    ("distinct_observations", result.distinct_observations),
                    ("latest_age_ns", result.latest_age_ns),
                    ("largest_confirmation_gap_ns", result.largest_confirmation_gap_ns),
                )
            ),
            "Candidates are not quality-approved confirmations.",
            *(f"CANDIDATE observed_at_ns={item.observed_at_ns} sg={item.sg}"
              for item in result.evidence),
            "No fermentation-complete, packaging, or actuation permission "
            "is granted by any status.",
            "No RAPT or Shelly device command was sent.",
        ]
    except (RaptError, httpx.HTTPError, ArithmeticError, OSError, TypeError, ValueError):
        typer.echo("Vessel SG diagnostic failed: request or response is invalid.", err=True)
        raise typer.Exit(1) from None
    typer.echo("\n".join(lines))


def _validate_brewforge_diagnostic_readings(
    readings: tuple[TelemetryReading, ...], *, brew_id: str, start_ns: int, end_ns: int,
    require_in_window: bool = True,
) -> None:
    """Check the complete adapter boundary before invoking any SG evaluation."""
    if not isinstance(readings, tuple) or not readings:
        raise ValueError("nonempty adapted tuple required")
    identifiers: set[str] = set()
    instants: set[int] = set()
    for item in readings:
        if not isinstance(item, TelemetryReading) or (
            item.source != "brewforge"
            or item.device_kind is not DeviceKind.HYDROMETER
            or item.device_id != brew_id
            or item.gravity_unit != "sg"
            or item.temperature_unit != "c"
        ):
            raise ValueError("unexpected stream or declaration")
        if (
            not isinstance(item.observed_at, datetime)
            or item.observed_at.utcoffset() is None
            or type(item.observed_at_submicrosecond_ns) is not int
            or item.observed_at_submicrosecond_ns not in range(0, 1000, 100)
        ):
            raise ValueError("invalid observation time")
        identifier = _canonical_reading_id(item.reading_id)
        instant = exact_ns(item)
        if identifier in identifiers or instant in instants:
            raise ValueError("duplicate identity, time, or out-of-window observation")
        if require_in_window and not start_ns <= instant <= end_ns:
            raise ValueError("out-of-window observation")
        if _measurement(item.gravity_raw, field="gravity") is None:
            raise ValueError("missing SG")
        _measurement(item.temperature_c, field="temperature")
        identifiers.add(identifier)
        instants.add(instant)


@vessel_app.command("sg-diagnose-brewforge")
def vessel_sg_diagnose_brewforge(
    vessel_id: str,
    start: Annotated[str, typer.Option("--start", help="Timezone-aware inclusive start.")],
    end: Annotated[str, typer.Option("--end", help="Inclusive end and evaluation time.")],
    trigger_sg: Annotated[str, typer.Option("--trigger-sg")],
    max_age_minutes: Annotated[float, typer.Option("--max-age-minutes")],
    max_gap_minutes: Annotated[float, typer.Option("--max-gap-minutes")],
    confirmations: Annotated[int, typer.Option("--confirmations", min=2, max=5)],
    gravity_unit: Annotated[str, typer.Option("--gravity-unit")],
    temperature_unit: Annotated[str, typer.Option("--temperature-unit")],
) -> None:
    """EXPERIMENTAL read-only BrewForge SG diagnostics; never permission to act."""
    try:
        if gravity_unit != "sg" or temperature_unit != "c":
            raise ValueError("explicit sg and c declarations required")
        start_at, start_remainder = _utc_timestamp(start)
        end_at, end_remainder = _utc_timestamp(end)
        start_ns = _datetime_ns(start_at) + start_remainder
        now_ns = _datetime_ns(end_at) + end_remainder
        if not 0 < now_ns - start_ns <= 7 * 86_400 * 1_000_000_000:
            raise ValueError("invalid diagnostic interval")
        trigger = Decimal(trigger_sg)
        if not trigger.is_finite() or not Decimal("0.9") <= trigger <= Decimal("1.2"):
            raise ValueError("invalid trigger")
        max_age_ns = _validated_max_age(max_age_minutes)
        max_gap_ns = _validated_max_age(max_gap_minutes)
        if min(max_age_ns, max_gap_ns) <= 0 or not 2 <= confirmations <= 5:
            raise ValueError("invalid diagnostic policy")
        contexts = [
            item for item in load_fermentation_contexts()
            if item["vessel_id"] == vessel_id and item["status"] == "active"
        ]
        if len(contexts) != 1:
            raise ValueError("one active context required")
        context_phase_history(contexts[0])
        brew_id = _canonical_brew_id(contexts[0].get("brewforge_brew_id"))
    except (ArithmeticError, OSError, TypeError, ValueError):
        typer.echo(
            "BrewForge SG diagnostic failed: input or active context is invalid; "
            "use vessel context start-brewforge for a valid BrewForge context.", err=True,
        )
        raise typer.Exit(1) from None

    try:
        client = BrewForgeClient(token=_token_for_api())
        payload = client.get(f"brews/{brew_id}/readings")
        readings = adapt_brewforge_readings(
            payload, brew_id=brew_id, gravity_unit=gravity_unit, temperature_unit=temperature_unit,
        )
        _validate_brewforge_diagnostic_readings(
            readings, brew_id=brew_id, start_ns=start_ns, end_ns=now_ns,
            require_in_window=False,
        )
        in_window_readings = tuple(
            item for item in readings if start_ns <= exact_ns(item) <= now_ns
        )
        _validate_brewforge_diagnostic_readings(
            in_window_readings, brew_id=brew_id, start_ns=start_ns, end_ns=now_ns,
        )
        result = explain_telemetry_sg(
            in_window_readings, gravity_unit="sg", now_ns=now_ns, trigger_sg=trigger,
            max_age_ns=max_age_ns, max_gap_ns=max_gap_ns, confirmations=confirmations,
        )
        if result.distinct_observations is None:
            raise ValueError("SG series integrity failure")
        lines = [
            "BrewForge SG diagnostic read-only (EXPERIMENTAL).",
            "source=brewforge gravity_unit=sg temperature_unit=c",
            f"Window: start_at_ns={start_ns} end_at_ns={now_ns}",
            f"evaluation_at_ns={now_ns} (query-end coverage, not live freshness)",
            "Declared units are caller assertions, not unit proof or verified calibration.",
            "The brew UUID identifies a stored stream, not a physical device.",
            "SG-only window; no batch or phase attribution is inferred.",
            f"trigger_sg={trigger} max_age_ns={max_age_ns} max_gap_ns={max_gap_ns} "
            f"confirmations={confirmations}",
            f"status={result.status} reason={result.reason}",
            " ".join(
                f"{name}={value if value is not None else 'NO_DATA'}"
                for name, value in (
                    ("distinct_observations", result.distinct_observations),
                    ("latest_age_ns", result.latest_age_ns),
                    ("largest_confirmation_gap_ns", result.largest_confirmation_gap_ns),
                )
            ),
            "Candidates are not quality-approved confirmations.",
            *(f"CANDIDATE observed_at_ns={item.observed_at_ns} sg={item.sg}"
              for item in result.evidence),
            "No fermentation-complete, packaging, safety, or actuation permission "
            "is granted by any status.",
            "No BrewForge, RAPT, or Shelly write and no device command was sent.",
        ]
    except (httpx.HTTPError, ArithmeticError, OSError, RecursionError, TypeError, ValueError):
        typer.echo("BrewForge SG diagnostic failed: request or response is invalid.", err=True)
        raise typer.Exit(1) from None
    typer.echo("\n".join(lines))


@vessel_app.command("sg-trend")
def vessel_sg_trend(
    vessel_id: str,
    start: Annotated[str, typer.Option("--start", help="Timezone-aware inclusive start.")],
    end: Annotated[
        str, typer.Option("--end", help="Timezone-aware inclusive end (maximum 7 days).")
    ],
    phase_timezone: Annotated[
        str,
        typer.Option(
            "--phase-timezone",
            help="Required IANA zone used only to identify ambiguous date-only boundary days.",
        ),
    ],
) -> None:
    """Describe endpoint SG trends by recorded phase; never infer completion or readiness."""
    try:
        start_at = _utc_datetime(start)
        end_at = _utc_datetime(end)
        if start_at >= end_at or end_at - start_at > MAX_TREND_WINDOW:
            raise ValueError("invalid trend interval")
        timezone = ZoneInfo(phase_timezone)
        bindings = load_vessels()
        binding = next(item for item in bindings if item["vessel_id"] == vessel_id)
        contexts = [
            item
            for item in load_fermentation_contexts()
            if item["vessel_id"] == vessel_id and item["status"] == "active"
        ]
        if len(contexts) != 1 or binding["gravity_interpretation"] != "sg-times-1000":
            raise ValueError("trend precondition failed")
        context = contexts[0]
        if context_binding_status(context, bindings) != "current":
            raise ValueError("context binding drift")
        sources = context["source_devices"]
        if not isinstance(sources, dict) or sources.get("hydrometer") != binding["hydrometer"]:
            raise ValueError("invalid context source")
        phases = context_phase_history(context)
    except (OSError, StopIteration, TypeError, ValueError, ZoneInfoNotFoundError):
        typer.echo(
            "Vessel SG trend failed: interval, IANA phase timezone, active context, "
            "current snapshotted binding, or SG interpretation is invalid.",
            err=True,
        )
        raise typer.Exit(1) from None

    profile = _profile_for_api()
    try:
        readings = _read_hydrometer(
            profile=profile,
            device_id=binding["hydrometer"],
            start=start_at,
            end=end_at,
        )
        report = build_trend_report(
            readings,
            phase_history=phases,
            phase_timezone=timezone,
            query_end=end_at,
        )
    except (RaptError, TelemetryValidationError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Vessel SG trend failed: request or response is invalid.", err=True)
        raise typer.Exit(1) from None

    typer.echo("Vessel SG trend read-only (experimental).")
    typer.echo(f"Vessel: {binding['vessel_id']}")
    typer.echo(f"Window: {start_at.isoformat()} / {end_at.isoformat()} (maximum 7 days)")
    typer.echo(
        f"Phase timezone: {phase_timezone} "
        "(date interpretation only; no phase start clock is known)"
    )
    typer.echo(
        f"excluded_boundary_observations={report.excluded_boundary_observations} "
        f"unattributed_before_start={report.unattributed_before_start}"
    )
    if not report.segments:
        typer.echo("No usable phase-attributed observations in the requested window.")
    for segment in report.segments:
        typer.echo(_trend_segment_line(segment))
    typer.echo(
        "Quality policy: gaps and query-end coverage over 90 minutes make endpoint trend NO_TREND; "
        "coverage is historical-window coverage, not live freshness."
    )
    typer.echo(
        "No fermentation-complete, bottling, switching, or safety conclusion is made; "
        "cold-crash stability is not evidence that fermentation is complete."
    )
    typer.echo("No RAPT or Shelly device command was sent.")
