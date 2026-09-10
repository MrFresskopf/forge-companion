"""Experimental offline vessel associations and read-only online telemetry."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Annotated

import httpx
import typer

from forge_companion import rapt_credentials
from forge_companion.cli_rapt import _profile_for_api, _utc_datetime
from forge_companion.fermentation_contexts import (
    FermentationContextBusyError,
    append_fermentation_phase,
    close_fermentation_context,
    context_binding_status,
    context_phase_history,
    load_fermentation_contexts,
    start_fermentation_context,
)
from forge_companion.rapt import RaptClient, RaptError
from forge_companion.telemetry import (
    DeviceKind,
    RaptTelemetrySource,
    TelemetryReading,
    TelemetryValidationError,
)
from forge_companion.vessels import VesselBusyError, bind_vessel, load_vessels

vessel_app = typer.Typer(help="Experimental vessel bindings and read-only telemetry.")
context_app = typer.Typer(help="Experimental offline fermentation contexts.")
vessel_app.add_typer(context_app, name="context")

STATUS_WINDOW_HOURS = 48
DEFAULT_PILL_MAX_AGE_MINUTES = 90.0
DEFAULT_CONTROLLER_MAX_AGE_MINUTES = 30.0
MAX_FRESHNESS_MINUTES = 2880.0


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
    # A future reading should already have failed the source's exact window validation.
    if age_ns < 0:
        raise TelemetryValidationError("RAPT telemetry lies outside the requested window")
    status = "CURRENT" if age_ns <= maximum_age_ns else "STALE"
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
