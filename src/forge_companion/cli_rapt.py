"""Typer commands for read-only RAPT telemetry."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import httpx
import typer

from forge_companion import rapt_credentials
from forge_companion.rapt import RaptClient, RaptError, RaptResponseError
from forge_companion.telemetry import (
    DeviceKind,
    RaptTelemetrySource,
    TelemetryReading,
    TelemetryValidationError,
)
from forge_companion.terminal_text import safe_terminal_text

rapt_app = typer.Typer(
    help="Read RAPT Pill and Temperature Controller telemetry without controlling devices.",
    no_args_is_help=False,
    invoke_without_command=True,
)
rapt_auth_app = typer.Typer(
    help="Manage RAPT API authentication without displaying credentials.",
    no_args_is_help=False,
    invoke_without_command=True,
)
rapt_app.add_typer(rapt_auth_app, name="auth")


@rapt_app.callback()
def rapt_command(context: typer.Context) -> None:
    """Read RAPT telemetry without changing RAPT or Shelly devices."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


def _profile_for_api() -> rapt_credentials.RaptProfile:
    try:
        resolved = rapt_credentials.resolve_profile()
    except rapt_credentials.RaptCredentialError:
        typer.echo("RAPT request failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    if resolved.profile is None:
        typer.echo(
            "RAPT request failed: run `forge-companion rapt auth login`.",
            err=True,
        )
        raise typer.Exit(code=2)
    return resolved.profile


def _device_line(kind: str, item: dict[str, object]) -> str:
    identifier = item.get("id")
    name = item.get("name")
    if not isinstance(identifier, str) or str(UUID(identifier)) != identifier:
        raise RaptResponseError("RAPT returned an invalid device payload")
    if not isinstance(name, str) or not name.strip():
        raise RaptResponseError("RAPT returned an invalid device payload")
    return f"{kind} {identifier} {safe_terminal_text(name)}"


@rapt_app.command("devices")
def rapt_devices_command() -> None:
    """List RAPT Pills and Temperature Controllers without controlling them."""
    profile = _profile_for_api()
    try:
        with RaptClient(username=profile.username, api_secret=profile.api_secret) as client:
            hydrometers = client.list_hydrometers()
            controllers = client.list_temperature_controllers()
        lines = [
            *(_device_line("HYDROMETER", item) for item in hydrometers),
            *(_device_line("TEMPERATURE_CONTROLLER", item) for item in controllers),
        ]
    except (RaptError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("RAPT device listing failed: request or response is invalid.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("RAPT devices read-only.")
    for line in lines:
        typer.echo(line)
    typer.echo("No RAPT or Shelly device command was sent.")


def _utc_datetime(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _reading_line(reading: TelemetryReading) -> str:
    fields = [reading.observed_at.isoformat()]
    if reading.temperature_c is not None:
        fields.append(f"temperature={reading.temperature_c:.1f} C")
    if reading.gravity_raw is not None:
        fields.append(f"gravity_raw={reading.gravity_raw:.4f}")
    if reading.target_temperature_c is not None:
        fields.append(f"target={reading.target_temperature_c:.1f} C")
    if reading.control_temperature_c is not None:
        fields.append(f"control={reading.control_temperature_c:.1f} C")
    if reading.battery_percent is not None:
        fields.append(f"battery={reading.battery_percent:.1f}%")
    if reading.rssi is not None:
        fields.append(f"rssi={reading.rssi:.1f}")
    return " ".join(fields)


@rapt_app.command("telemetry")
def rapt_telemetry_command(
    device_kind: Annotated[
        str,
        typer.Argument(help="RAPT device type: hydrometer or temperature-controller."),
    ],
    device_id: Annotated[str, typer.Argument(help="Canonical RAPT device UUID.")],
    start: Annotated[str, typer.Option("--start", help="Inclusive timezone-aware start.")],
    end: Annotated[
        str, typer.Option("--end", help="Inclusive validation boundary for the timezone-aware end.")
    ],
) -> None:
    """Read and normalize one explicit RAPT telemetry window."""
    try:
        kind = DeviceKind(device_kind)
        start_at = _utc_datetime(start)
        end_at = _utc_datetime(end)
        if str(UUID(device_id)) != device_id or start_at >= end_at:
            raise ValueError("Invalid device or interval")
        profile = _profile_for_api()
        with RaptClient(username=profile.username, api_secret=profile.api_secret) as client:
            source = RaptTelemetrySource(client=client, kind=kind)
            readings = source.get_readings(
                device_id=device_id,
                start=start_at,
                end=end_at,
            )
    except (
        RaptError,
        TelemetryValidationError,
        httpx.HTTPError,
        OSError,
        TypeError,
        ValueError,
    ):
        typer.echo("RAPT telemetry failed: input, request, or response is invalid.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("RAPT telemetry read-only.")
    typer.echo(f"Device: {kind.name} {device_id}")
    typer.echo(f"Window: {start_at.isoformat()} / {end_at.isoformat()}")
    typer.echo(f"Readings: {len(readings)}")
    for reading in readings:
        typer.echo(_reading_line(reading))
    typer.echo("No RAPT or Shelly device command was sent.")


@rapt_auth_app.callback()
def rapt_auth_command(context: typer.Context) -> None:
    """Manage RAPT API authentication without displaying credentials."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


@rapt_auth_app.command("login")
def rapt_auth_login_command() -> None:
    """Store one RAPT API profile in the native OS credential store."""
    username = typer.prompt("RAPT account email")
    api_secret = typer.prompt("RAPT API secret", hide_input=True, confirmation_prompt=True)
    try:
        rapt_credentials.store_profile(username=username, api_secret=api_secret)
    except ValueError:
        typer.echo("RAPT login failed: profile values are invalid.", err=True)
        raise typer.Exit(code=1) from None
    except rapt_credentials.RaptCredentialError:
        typer.echo("RAPT login failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("RAPT profile stored in the native OS credential store.")


@rapt_auth_app.command("status")
def rapt_auth_status_command() -> None:
    """Show whether a RAPT profile exists without displaying its values."""
    try:
        resolved = rapt_credentials.resolve_profile()
    except rapt_credentials.RaptCredentialError:
        typer.echo("RAPT authentication status failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    if resolved.profile is None:
        typer.echo("RAPT profile is not configured.", err=True)
        raise typer.Exit(code=1)
    typer.echo("RAPT profile is configured in the native OS credential store.")


@rapt_auth_app.command("logout")
def rapt_auth_logout_command() -> None:
    """Delete the stored RAPT profile."""
    try:
        deleted = rapt_credentials.delete_profile()
    except rapt_credentials.RaptCredentialError:
        typer.echo("RAPT logout failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    if deleted:
        typer.echo("Stored RAPT profile deleted.")
    else:
        typer.echo("No stored RAPT profile was present.")
