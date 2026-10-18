"""Experimental read-only Brewfather discovery commands."""

from __future__ import annotations

from math import isfinite
from typing import Annotated

import httpx
import typer

from forge_companion import brewfather_credentials
from forge_companion.brewfather_client import (
    BrewfatherClient,
    BrewfatherError,
    BrewfatherRequestError,
)
from forge_companion.brewfather_telemetry import adapt_brewfather_readings
from forge_companion.telemetry import TelemetryValidationError

brewfather_app = typer.Typer(
    help="Experimental read-only Brewfather discovery for native RAPT Brew data.",
    no_args_is_help=False,
    invoke_without_command=True,
)
brewfather_auth_app = typer.Typer(
    help="Manage Brewfather API credentials without displaying them.",
    no_args_is_help=False,
    invoke_without_command=True,
)
brewfather_app.add_typer(brewfather_auth_app, name="auth")


@brewfather_app.callback()
def brewfather_command(context: typer.Context) -> None:
    """Inspect Brewfather data without writes or device commands."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


@brewfather_auth_app.callback()
def brewfather_auth_command(context: typer.Context) -> None:
    """Manage native Brewfather API credentials without displaying them."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


@brewfather_auth_app.command("login")
def brewfather_auth_login_command() -> None:
    """Store one Brewfather user ID and API key in the native credential store."""
    user_id = typer.prompt("Brewfather user ID", hide_input=True)
    api_key = typer.prompt("Brewfather API key", hide_input=True, confirmation_prompt=True)
    try:
        brewfather_credentials.store_profile(user_id=user_id, api_key=api_key)
    except ValueError:
        typer.echo("Brewfather login failed: profile values are invalid.", err=True)
        raise typer.Exit(code=1) from None
    except brewfather_credentials.BrewfatherCredentialError:
        typer.echo("Brewfather login failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Brewfather profile stored in the native OS credential store.")


@brewfather_auth_app.command("status")
def brewfather_auth_status_command() -> None:
    """Show whether a Brewfather profile is configured without displaying it."""
    try:
        resolved = brewfather_credentials.resolve_profile()
    except brewfather_credentials.BrewfatherCredentialError:
        typer.echo("Brewfather status failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    if resolved.profile is None:
        typer.echo("Brewfather profile is not configured.", err=True)
        raise typer.Exit(code=1)
    typer.echo("Brewfather profile is configured in the native OS credential store.")


@brewfather_auth_app.command("logout")
def brewfather_auth_logout_command() -> None:
    """Delete the stored Brewfather API profile."""
    try:
        deleted = brewfather_credentials.delete_profile()
    except brewfather_credentials.BrewfatherCredentialError:
        typer.echo("Brewfather logout failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    if deleted:
        typer.echo("Stored Brewfather profile deleted.")
    else:
        typer.echo("No stored Brewfather profile was present.")


def _profile_for_api() -> brewfather_credentials.BrewfatherProfile:
    try:
        resolved = brewfather_credentials.resolve_profile()
    except brewfather_credentials.BrewfatherCredentialError:
        typer.echo("Brewfather request failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    if resolved.profile is None:
        typer.echo(
            "Brewfather request failed: run `forge-companion brewfather auth login`.",
            err=True,
        )
        raise typer.Exit(code=2)
    return resolved.profile


def _schema_summary(records: list[dict[str, object]]) -> str:
    fields: dict[str, set[str]] = {}
    for record in records:
        for name, value in record.items():
            if not isinstance(name, str):
                raise ValueError("Brewfather returned an invalid readings payload")
            fields.setdefault(name, set()).add(type(value).__name__)
    rendered = ", ".join(
        f"{name}={'|'.join(sorted(types))}" for name, types in sorted(fields.items())
    )
    return rendered or "<none>"


def _display_identifier(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(not character.isprintable() for character in value)
    ):
        raise ValueError(f"invalid {field}")
    return value


def _telemetry_validation_label(error: TelemetryValidationError) -> str:
    """Return only an allowlisted local adapter reason, never upstream data."""
    message = str(error)
    allowed = {
        "Brewfather telemetry contains an invalid timestamp": "invalid timestamp",
        "Brewfather telemetry contains an invalid gravity": "invalid gravity",
        "Brewfather telemetry contains an invalid temperature": "invalid temperature",
        "Brewfather telemetry contains an invalid device type": "invalid device type",
        "Brewfather telemetry contains duplicate reading identifiers": (
            "duplicate reading identifier"
        ),
        "Brewfather telemetry has no selected device records": "no selected device records",
        "Brewfather telemetry response is not a collection": "invalid response collection",
        "Brewfather telemetry units must be explicit: sg, c, and epoch-ms": (
            "invalid unit declaration"
        ),
    }
    return allowed.get(message, "validation failure")


def _exclude_outside_declared_sg(
    records: list[dict[str, object]], *, device_id: str
) -> tuple[list[dict[str, object]], int, int]:
    """Keep raw records but exclude only impossible values for an explicit SG declaration."""
    accepted: list[dict[str, object]] = []
    below = 0
    above = 0
    for record in records:
        value = record.get("sg") if record.get("deviceId") == device_id else None
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(float(value))
        ):
            if value < 0.9:
                below += 1
                continue
            if value > 1.2:
                above += 1
                continue
        accepted.append(record)
    return accepted, below, above


@brewfather_app.command("devices")
def brewfather_devices_command(
    batch_id: Annotated[str, typer.Argument(help="One exact Brewfather batch ID.")],
) -> None:
    """List source IDs/types found in one batch without displaying device names or readings."""
    profile = _profile_for_api()
    try:
        with BrewfatherClient(user_id=profile.user_id, api_key=profile.api_key) as client:
            records = client.get_batch_readings(batch_id)
        devices: dict[str, tuple[str, int]] = {}
        for record in records:
            device_id = _display_identifier(record.get("deviceId"), field="device identifier")
            device_type = _display_identifier(record.get("deviceType"), field="device type")
            previous = devices.get(device_id)
            if previous is not None and previous[0] != device_type:
                raise ValueError("conflicting device type")
            devices[device_id] = (device_type, 1 if previous is None else previous[1] + 1)
    except BrewfatherRequestError as error:
        typer.echo(
            f"Brewfather device listing failed: request rejected (HTTP {error.status_code}).",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except (BrewfatherError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Brewfather device listing failed: request or response is invalid.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Brewfather batch devices (read-only).")
    for device_id, (device_type, count) in sorted(devices.items()):
        typer.echo(f"device_id={device_id} device_type={device_type} records={count}")


@brewfather_app.command("telemetry-summary")
def brewfather_telemetry_summary_command(
    batch_id: Annotated[str, typer.Argument(help="One exact Brewfather batch ID.")],
    device_id: Annotated[str, typer.Argument(help="One exact device ID from brewfather devices.")],
    gravity_unit: Annotated[str, typer.Option("--gravity-unit")],
    temperature_unit: Annotated[str, typer.Option("--temperature-unit")],
    time_unit: Annotated[str, typer.Option("--time-unit")],
) -> None:
    """Validate and summarize one explicit native Pill stream without displaying measurements."""
    profile = _profile_for_api()
    try:
        with BrewfatherClient(user_id=profile.user_id, api_key=profile.api_key) as client:
            records = client.get_batch_readings(batch_id)
        accepted_records, rejected_below, rejected_above = _exclude_outside_declared_sg(
            records, device_id=device_id
        )
        readings = adapt_brewfather_readings(
            accepted_records,
            device_id=device_id,
            gravity_unit=gravity_unit,
            temperature_unit=temperature_unit,
            time_unit=time_unit,
        )
    except BrewfatherRequestError as error:
        typer.echo(
            f"Brewfather telemetry summary failed: request rejected (HTTP {error.status_code}).",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except TelemetryValidationError as error:
        typer.echo(
            "Brewfather telemetry summary failed: selected stream is invalid "
            f"({_telemetry_validation_label(error)}).",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except (BrewfatherError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Brewfather telemetry summary failed: request or response is invalid.", err=True)
        raise typer.Exit(code=1) from None
    gravity_present = sum(item.gravity_raw is not None for item in readings)
    temperature_present = sum(item.temperature_c is not None for item in readings)
    typer.echo("Brewfather Pill telemetry read-only (experimental).")
    typer.echo(f"Device: {device_id}")
    typer.echo(f"Records: {len(readings)}")
    typer.echo(
        f"Rejected declared-SG readings: below={rejected_below} above={rejected_above}"
    )
    typer.echo(f"First: {readings[0].observed_at_exact}")
    typer.echo(f"Latest: {readings[-1].observed_at_exact}")
    typer.echo(f"SG: present={gravity_present} missing={len(readings) - gravity_present}")
    typer.echo(
        f"Temperature: present={temperature_present} missing={len(readings) - temperature_present}"
    )


@brewfather_app.command("gravity-audit")
def brewfather_gravity_audit_command(
    batch_id: Annotated[str, typer.Argument(help="One exact Brewfather batch ID.")],
    device_id: Annotated[str, typer.Argument(help="One exact device ID from brewfather devices.")],
) -> None:
    """Classify only the selected stream's gravity scale without showing values."""
    profile = _profile_for_api()
    try:
        with BrewfatherClient(user_id=profile.user_id, api_key=profile.api_key) as client:
            records = client.get_batch_readings(batch_id)
        selected = [record for record in records if record.get("deviceId") == device_id]
        if not selected:
            raise ValueError("selected device absent")
        numeric: list[float] = []
        missing = 0
        invalid = 0
        for record in selected:
            value = record.get("sg")
            if value is None:
                missing += 1
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                invalid += 1
            else:
                number = float(value)
                if not isfinite(number):
                    invalid += 1
                else:
                    numeric.append(number)
    except BrewfatherRequestError as error:
        typer.echo(
            f"Brewfather gravity audit failed: request rejected (HTTP {error.status_code}).",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except (BrewfatherError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Brewfather gravity audit failed: request or response is invalid.", err=True)
        raise typer.Exit(code=1) from None
    sg_below = sum(value < 0.9 for value in numeric)
    sg_in_range = sum(0.9 <= value <= 1.2 for value in numeric)
    sg_above = sum(value > 1.2 for value in numeric)
    scaled_in_range = sum(900 <= value <= 1200 for value in numeric)
    sg_candidate = bool(numeric) and sg_in_range == len(numeric)
    scaled_candidate = bool(numeric) and scaled_in_range == len(numeric)
    typer.echo("Brewfather gravity audit read-only.")
    typer.echo(f"Records: {len(selected)}")
    typer.echo(f"SG: numeric={len(numeric)} missing={missing} invalid={invalid}")
    typer.echo(
        "Scale candidates: "
        f"sg={'YES' if sg_candidate else 'NO'} "
        f"sg-times-1000={'YES' if scaled_candidate else 'NO'}"
    )
    typer.echo(
        "Range counts: "
        f"sg={sg_in_range}/{len(numeric)} "
        f"sg-times-1000={scaled_in_range}/{len(numeric)}"
    )
    typer.echo(
        f"SG partition: below={sg_below} in-range={sg_in_range} above={sg_above}"
    )


@brewfather_app.command("readings-schema")
def brewfather_readings_schema_command(
    batch_id: Annotated[str, typer.Argument(help="One exact Brewfather batch ID.")],
) -> None:
    """Read one batch and print only its reading count, field names, and value types."""
    if batch_id == "BATCH_ID":
        typer.echo(
            "Brewfather readings schema failed: replace BATCH_ID with an actual batch ID.",
            err=True,
        )
        raise typer.Exit(code=2)
    profile = _profile_for_api()
    try:
        with BrewfatherClient(user_id=profile.user_id, api_key=profile.api_key) as client:
            records = client.get_batch_readings(batch_id)
        fields = _schema_summary(records)
    except BrewfatherRequestError as error:
        typer.echo(
            f"Brewfather readings schema failed: request rejected (HTTP {error.status_code}).",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except (BrewfatherError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Brewfather readings schema failed: request or response is invalid.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Brewfather readings schema (read-only).")
    typer.echo(f"Records: {len(records)}")
    typer.echo(f"Fields: {fields}")
