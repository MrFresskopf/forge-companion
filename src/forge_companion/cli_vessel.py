"""Experimental offline vessel associations and read-only online telemetry."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Annotated

import httpx
import typer

from forge_companion import rapt_credentials
from forge_companion.cli_rapt import _profile_for_api, _utc_datetime
from forge_companion.rapt import RaptClient, RaptError
from forge_companion.telemetry import (
    DeviceKind,
    RaptTelemetrySource,
    TelemetryReading,
    TelemetryValidationError,
)
from forge_companion.vessels import VesselBusyError, bind_vessel, load_vessels

vessel_app = typer.Typer(help="Experimental vessel bindings and read-only telemetry.")

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
