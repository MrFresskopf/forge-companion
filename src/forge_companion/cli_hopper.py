"""Typer commands for guarded hopper planning and Shelly control."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from time import sleep as sleep_seconds
from typing import Annotated, Any
from uuid import UUID

import httpx
import typer

from forge_companion import (
    preferences,
    shelly_cloud_credentials,
)
from forge_companion.cli_common import is_interactive_terminal as _is_interactive_terminal
from forge_companion.hopper import (
    HopperPlanBusyError,
    HopperPlanExistsError,
    HopperPlanValidationError,
    arm_hopper_plan,
    create_hopper_plan,
    fire_hopper_plan,
    hopper_plan_lock,
    load_hopper_plan,
    simulate_hopper_plan,
    validate_hopper_plan,
    write_hopper_plan,
    write_new_hopper_plan,
)
from forge_companion.shelly import ShellyReadOnlyClient, ShellyResponseError
from forge_companion.shelly_cloud import ShellyCloudReadOnlyClient, ShellyCloudResponseError
from forge_companion.terminal_text import safe_terminal_text

hopper_app = typer.Typer(
    help="Prepare, rehearse, and fire guarded remote-hopper plans.",
    no_args_is_help=False,
    invoke_without_command=True,
)
cloud_auth_app = typer.Typer(
    help="Manage Shelly Cloud authentication without displaying credentials.",
    no_args_is_help=False,
    invoke_without_command=True,
)
hopper_app.add_typer(cloud_auth_app, name="cloud-auth")
qualification_app = typer.Typer(
    help="Manage the operator-attested full-assembly qualification gate.",
    no_args_is_help=False,
    invoke_without_command=True,
)
hopper_app.add_typer(qualification_app, name="qualification")

@hopper_app.callback()
def hopper_command(context: typer.Context) -> None:
    """Prepare, rehearse, and fire guarded remote-hopper plans."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


@cloud_auth_app.callback()
def cloud_auth_command(context: typer.Context) -> None:
    """Manage Shelly Cloud authentication without displaying credentials."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


@qualification_app.callback()
def qualification_command(context: typer.Context) -> None:
    """Manage operator attestation without claiming sensor verification."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


@qualification_app.command("attest")
def qualification_attest_command() -> None:
    """Persist the operator's declaration that ten full-assembly tests succeeded."""
    if not _is_interactive_terminal():
        typer.echo(
            "Hopper qualification attestation blocked: an interactive terminal is required.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo("Confirm that 10 successful full-assembly tests were actually completed.")
    typer.echo("This includes complete release with no jam, stall, or unsafe endpoint impact.")
    typer.echo("You declare that a 1,000 ms pulse was sufficient in all 10 tests.")
    typer.echo("You declare that the 4-second device auto-off and 12 cm fault travel are safe.")
    typer.echo("You declare that manual electrical isolation remains immediately available.")
    typer.echo("Forge Companion cannot verify those mechanical results.")
    confirmation = typer.prompt("Type I CONFIRM 10 SUCCESSFUL TESTS to attest")
    if confirmation != "I CONFIRM 10 SUCCESSFUL TESTS":
        typer.echo("Hopper qualification attestation cancelled; no preference was changed.")
        raise typer.Exit(code=1)
    try:
        stored = preferences.load_preferences()
        preferences.save_preferences(
            replace(
                stored,
                hopper_qualification_statement_version=(
                    preferences.HOPPER_QUALIFICATION_STATEMENT_VERSION
                ),
                hopper_qualification_attested_at=datetime.now(UTC).isoformat(),
            )
        )
    except (OSError, preferences.PreferencesError):
        typer.echo("Hopper qualification attestation failed: local preferences are unavailable.")
        raise typer.Exit(code=1) from None
    typer.echo("Remote hopper qualification: OPERATOR ATTESTED")
    typer.echo("The operator declared 10 successful full-assembly tests.")
    typer.echo("No automatic or sensor-based verification was performed.")
    typer.echo("No device or network was contacted.")


@qualification_app.command("status")
def qualification_status_command() -> None:
    """Show the non-sensitive local attestation state without network access."""
    try:
        stored = preferences.load_preferences()
    except (OSError, preferences.PreferencesError):
        typer.echo("Hopper qualification status failed: local preferences are unavailable.")
        raise typer.Exit(code=1) from None
    if not preferences.hopper_qualification_is_current(stored):
        typer.echo("Remote hopper qualification is not operator-attested.")
        typer.echo("No device or network was contacted.")
        raise typer.Exit(code=1)
    typer.echo("Remote hopper qualification: OPERATOR ATTESTED")
    typer.echo("The operator declared 10 successful full-assembly tests.")
    typer.echo("No automatic or sensor-based verification was performed.")
    typer.echo("No device or network was contacted.")


@qualification_app.command("revoke")
def qualification_revoke_command() -> None:
    """Remove the local operator attestation without contacting hardware."""
    try:
        stored = preferences.load_preferences()
        preferences.save_preferences(
            replace(
                stored,
                hopper_qualification_statement_version=None,
                hopper_qualification_attested_at=None,
            )
        )
    except (OSError, preferences.PreferencesError):
        typer.echo("Hopper qualification revoke failed: local preferences are unavailable.")
        raise typer.Exit(code=1) from None
    typer.echo("Remote hopper qualification attestation revoked.")
    typer.echo("No device or network was contacted.")


def _require_current_hopper_qualification() -> None:
    try:
        stored = preferences.load_preferences()
    except (OSError, preferences.PreferencesError):
        typer.echo(
            "Hopper fire blocked: local qualification status is unavailable.",
            err=True,
        )
        raise typer.Exit(code=1) from None
    if not preferences.hopper_qualification_is_current(stored):
        typer.echo(
            "Hopper fire blocked: full-assembly qualification is not operator-attested.",
            err=True,
        )
        typer.echo("Run `forge-companion hopper qualification attest`.", err=True)
        raise typer.Exit(code=1)


@hopper_app.command("plan")
def hopper_plan_command(
    trigger_at: Annotated[
        str,
        typer.Option("--trigger-at", help="Timezone-aware ISO trigger time."),
    ],
    pulse_ms: Annotated[
        str,
        typer.Option("--pulse-ms", help="Pulse duration in milliseconds."),
    ],
    brew_id: Annotated[
        str | None,
        typer.Option(
            "--brew-id",
            help="Optional exact BrewForge brew UUID; no API request is made.",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination local plan file."),
    ] = Path("automation/hopper-plan.json"),
    cloud: Annotated[
        bool,
        typer.Option("--cloud", help="Create a one-shot plan for the stored Shelly Cloud profile."),
    ] = False,
) -> None:
    """Create an offline draft; --cloud binds it to the stored device without its key."""
    try:
        trigger = datetime.fromisoformat(trigger_at)
        canonical_brew_id = UUID(brew_id) if brew_id is not None else None
        server: str | None = None
        device_id: str | None = None
        if cloud:
            resolved = shelly_cloud_credentials.resolve_profile()
            if resolved.profile is None:
                raise ValueError("no stored Shelly Cloud profile")
            server = resolved.profile.server
            device_id = resolved.profile.device_id
        with hopper_plan_lock(output):
            payload = create_hopper_plan(
                trigger_at=trigger,
                pulse_duration_ms=int(pulse_ms),
                brew_id=canonical_brew_id,
                server=server,
                device_id=device_id,
            )
            write_new_hopper_plan(payload, output)
    except HopperPlanBusyError:
        typer.echo("Hopper plan failed: destination is busy or locked.", err=True)
        raise typer.Exit(code=1) from None
    except HopperPlanExistsError:
        typer.echo("Hopper plan failed: destination already exists.", err=True)
        raise typer.Exit(code=1) from None
    except shelly_cloud_credentials.ShellyCloudCredentialError:
        typer.echo("Hopper plan failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    except OSError:
        typer.echo("Hopper plan failed: local file operation failed.", err=True)
        raise typer.Exit(code=1) from None
    except (TypeError, ValueError):
        if cloud:
            typer.echo(
                "Hopper plan failed: trigger, pulse, brew UUID, or Cloud profile is invalid.",
                err=True,
            )
        else:
            typer.echo("Hopper plan failed: trigger, pulse, or brew UUID is invalid.", err=True)
        raise typer.Exit(code=1) from None
    if cloud:
        typer.echo("Hopper Cloud one-shot plan written.")
    else:
        typer.echo("Hopper simulation plan written.")
    typer.echo("Status: DRAFT")
    typer.echo("No device or network was contacted.")


@hopper_app.command("arm")
def hopper_arm_command(
    source: Annotated[Path, typer.Argument(help="Local hopper plan file.")],
) -> None:
    """Explicitly arm a valid future plan without contacting hardware."""
    try:
        with hopper_plan_lock(source):
            payload = load_hopper_plan(source)
            is_cloud = payload["action"]["kind"] == "cloud-pulse"
            armed = arm_hopper_plan(payload, at=datetime.now(UTC))
            write_hopper_plan(armed, source)
    except HopperPlanBusyError:
        typer.echo("Hopper arm failed: plan is busy or locked.", err=True)
        raise typer.Exit(code=1) from None
    except OSError:
        typer.echo("Hopper arm failed: local file operation failed.", err=True)
        raise typer.Exit(code=1) from None
    except (HopperPlanValidationError, TypeError, ValueError):
        typer.echo("Hopper arm failed: plan is invalid, expired, or not a draft.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Hopper Cloud one-shot plan armed." if is_cloud else "Hopper simulation plan armed.")
    typer.echo("Status: ARMED")
    typer.echo("No device or network was contacted.")


@hopper_app.command("simulate")
def hopper_simulate_command(
    source: Annotated[Path, typer.Argument(help="Local armed hopper plan file.")],
    at: Annotated[
        str | None,
        typer.Option(
            "--at",
            help="Optional timezone-aware simulation clock; never available to hardware actions.",
        ),
    ] = None,
) -> None:
    """Complete an armed simulation plan offline without sending a pulse."""
    try:
        simulation_time = datetime.fromisoformat(at) if at is not None else datetime.now(UTC)
        with hopper_plan_lock(source):
            payload = load_hopper_plan(source)
            completed = simulate_hopper_plan(payload, at=simulation_time)
            write_hopper_plan(completed, source)
    except HopperPlanBusyError:
        typer.echo("Hopper simulation failed: plan is busy or locked.", err=True)
        raise typer.Exit(code=1) from None
    except OSError:
        typer.echo("Hopper simulation failed: local file operation failed.", err=True)
        raise typer.Exit(code=1) from None
    except (HopperPlanValidationError, TypeError, ValueError):
        typer.echo(
            "Hopper simulation failed: plan is invalid, early, or not armed.",
            err=True,
        )
        raise typer.Exit(code=1) from None
    typer.echo("Hopper simulation completed.")
    typer.echo("Status: LOCKED")
    typer.echo("No device or network was contacted; no physical pulse was sent.")


@hopper_app.command("check")
def hopper_check_command(
    source: Annotated[Path, typer.Argument(help="Local armed Cloud one-shot plan file.")],
) -> None:
    """Check plan, credential binding, and live electrical readiness without switching."""
    from forge_companion import shelly_cloud

    try:
        payload = load_hopper_plan(source)
        summary = validate_hopper_plan(payload)
        action = payload["action"]
        if summary.status.value != "ARMED" or action.get("kind") != "cloud-pulse":
            raise ValueError("plan is not an armed cloud pulse")
        if datetime.now(UTC) < summary.trigger_at:
            raise ValueError("plan trigger has not been reached")

        resolved = shelly_cloud_credentials.resolve_profile()
        if resolved.profile is None:
            raise ValueError("Shelly Cloud credentials are not configured")
        profile = resolved.profile
        if profile.server != action["server"] or profile.device_id != action["device_id"]:
            raise ValueError("Shelly Cloud profile does not match the plan")

        with shelly_cloud.ShellyCloudReadOnlyClient(
            server=profile.server,
            device_id=profile.device_id,
            auth_key=profile.auth_key,
        ) as status_client:
            preflight = status_client.get_switch_status(channel=0)
        if not preflight.online or preflight.output is not False:
            typer.echo(
                "Hopper check failed: electrical preflight requires "
                "an online device reporting OFF.",
                err=True,
            )
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except shelly_cloud_credentials.ShellyCloudCredentialError:
        typer.echo("Hopper check failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    except (ShellyCloudResponseError, httpx.HTTPError, RuntimeError):
        typer.echo("Hopper check failed: Cloud status request failed.", err=True)
        raise typer.Exit(code=1) from None
    except (HopperPlanValidationError, OSError, TypeError, ValueError):
        typer.echo(
            "Hopper check failed: plan, trigger, or Cloud profile is not ready.",
            err=True,
        )
        raise typer.Exit(code=1) from None

    typer.echo("Hopper Cloud one-shot readiness check passed.")
    typer.echo("Status: ARMED")
    typer.echo("Trigger: reached")
    typer.echo(f"Pulse: {summary.pulse_duration_ms} ms")
    typer.echo("Credential target: matched")
    typer.echo("Electrical preflight: ONLINE / OFF")
    typer.echo("Mechanical release: NOT VERIFIED")
    typer.echo("No switch command was sent and the plan was not changed.")


@hopper_app.command("fire")
def hopper_fire_command(
    source: Annotated[Path, typer.Argument(help="Local armed Cloud one-shot plan file.")],
) -> None:
    """Send one attested and explicitly confirmed Cloud pulse; never retry automatically."""
    from forge_companion import shelly_cloud

    if not _is_interactive_terminal():
        typer.echo("Hopper fire blocked: an interactive terminal is required.", err=True)
        raise typer.Exit(code=1)

    fire_requested = False
    try:
        with hopper_plan_lock(source):
            payload = load_hopper_plan(source)
            summary = validate_hopper_plan(payload)
            action = payload["action"]
            if summary.status.value != "ARMED" or action.get("kind") != "cloud-pulse":
                raise ValueError("plan is not an armed cloud pulse")
            if datetime.now(UTC) < summary.trigger_at:
                raise ValueError("plan trigger has not been reached")

            _require_current_hopper_qualification()

            typer.echo(
                f"Ready to send one {summary.pulse_duration_ms} ms Cloud pulse on channel 0."
            )
            confirmation = typer.prompt("Type FIRE to send one one-shot pulse")
            if confirmation != "FIRE":
                typer.echo("Hopper fire cancelled; no switch command was sent.")
                raise typer.Exit(code=1)

            resolved = shelly_cloud_credentials.resolve_profile()
            if resolved.profile is None:
                raise ValueError("Shelly Cloud credentials are not configured")
            profile = resolved.profile
            if profile.server != action["server"] or profile.device_id != action["device_id"]:
                raise ValueError("Shelly Cloud profile does not match the plan")

            preflight_started = monotonic()
            with shelly_cloud.ShellyCloudReadOnlyClient(
                server=profile.server,
                device_id=profile.device_id,
                auth_key=profile.auth_key,
            ) as status_client:
                preflight = status_client.get_switch_status(channel=0)
            if not preflight.online or preflight.output is not False:
                typer.echo(
                    "Hopper fire blocked: preflight requires an online device reporting OFF.",
                    err=True,
                )
                raise typer.Exit(code=1)

            elapsed = monotonic() - preflight_started
            if elapsed < shelly_cloud.CLOUD_REQUEST_INTERVAL_SECONDS:
                sleep_seconds(shelly_cloud.CLOUD_REQUEST_INTERVAL_SECONDS - elapsed)

            def persist(changed: dict[str, Any]) -> None:
                nonlocal fire_requested
                write_hopper_plan(changed, source)
                if changed["state"]["status"] == "FIRE_REQUESTED":
                    fire_requested = True

            with shelly_cloud.ShellyCloudActuator(
                server=profile.server,
                device_id=profile.device_id,
                auth_key=profile.auth_key,
            ) as actuator:
                fire_hopper_plan(
                    payload,
                    at=datetime.now(UTC),
                    persist=persist,
                    actuator=actuator,
                )
    except typer.Exit:
        raise
    except HopperPlanBusyError:
        typer.echo("Hopper fire failed: plan is busy or locked.", err=True)
        raise typer.Exit(code=1) from None
    except shelly_cloud_credentials.ShellyCloudCredentialError:
        typer.echo(
            "Hopper fire failed: credential store access failed; no retry was sent.",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except (
        ShellyCloudResponseError,
        httpx.HTTPError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        if fire_requested:
            typer.echo(
                "Hopper fire outcome is uncertain; the plan remains FIRE_REQUESTED. "
                "Do not retry automatically.",
                err=True,
            )
        else:
            typer.echo("Hopper fire failed before a pulse request was recorded.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Hopper Cloud one-shot completed.")
    typer.echo("Status: LOCKED")
    typer.echo("Electrical read-back: OFF")
    typer.echo("This does not prove mechanical hop release.")


@hopper_app.command("status")
def hopper_status_command(
    source: Annotated[Path, typer.Argument(help="Local hopper plan file.")],
) -> None:
    """Validate a local hopper plan and show non-sensitive metadata."""
    try:
        payload = load_hopper_plan(source)
        summary = validate_hopper_plan(payload)
        is_cloud = payload["action"]["kind"] == "cloud-pulse"
    except (HopperPlanValidationError, OSError, TypeError, ValueError):
        typer.echo("Hopper status failed: plan is invalid or unreadable.", err=True)
        raise typer.Exit(code=1) from None
    if is_cloud:
        typer.echo("Hopper Cloud one-shot plan is valid.")
    else:
        typer.echo("Hopper simulation plan is valid.")
    typer.echo(f"Status: {summary.status.value}")
    typer.echo(f"Trigger: {summary.trigger_at.isoformat()}")
    pulse_kind = "Cloud one-shot" if is_cloud else "simulation only"
    typer.echo(f"Pulse: {summary.pulse_duration_ms} ms ({pulse_kind})")
    typer.echo("No device or network was contacted.")


@hopper_app.command("shelly-status")
def hopper_shelly_status_command(
    device_url: Annotated[
        str,
        typer.Option("--device-url", help="Base URL of the local Shelly device."),
    ],
    channel: Annotated[
        str,
        typer.Option("--channel", help="Shelly switch channel to read."),
    ] = "0",
) -> None:
    """Read local Shelly switch status without sending a switch command."""
    try:
        channel_id = int(channel)
        if channel_id < 0:
            raise ValueError("channel must not be negative")
        with ShellyReadOnlyClient(base_url=device_url) as client:
            status = client.get_switch_status(channel=channel_id)
    except (ShellyResponseError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Shelly status failed: device, channel, or response is invalid.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Shelly status read-only.")
    typer.echo(f"Channel: {status.channel}")
    typer.echo(f"Output: {'ON' if status.output else 'OFF'}")
    typer.echo(f"Source: {safe_terminal_text(status.source)}")
    typer.echo(f"Switch-on count: {status.switch_on_count}")
    typer.echo(f"Temperature: {status.temperature_c:.1f} C")
    typer.echo("No switch command was sent.")


@hopper_app.command("cloud-status")
def hopper_cloud_status_command(
    channel: Annotated[
        int,
        typer.Option("--channel", min=0, max=255, help="Shelly switch channel to read."),
    ] = 0,
) -> None:
    """Read Shelly switch status through Cloud v2 without sending a switch command."""
    try:
        resolved = shelly_cloud_credentials.resolve_profile()
        if resolved.profile is None:
            typer.echo(
                "Shelly Cloud status failed: run `forge-companion hopper cloud-auth login`.",
                err=True,
            )
            raise typer.Exit(code=2)
        profile = resolved.profile
        with ShellyCloudReadOnlyClient(
            server=profile.server,
            device_id=profile.device_id,
            auth_key=profile.auth_key,
        ) as client:
            status = client.get_switch_status(channel=channel)
    except typer.Exit:
        raise
    except shelly_cloud_credentials.ShellyCloudCredentialError:
        typer.echo("Shelly Cloud status failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    except (ShellyCloudResponseError, httpx.HTTPError, OSError, TypeError, ValueError):
        typer.echo("Shelly Cloud status failed: request or response is invalid.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Shelly Cloud status read-only.")
    typer.echo(f"Online: {'YES' if status.online else 'NO'}")
    typer.echo(f"Channel: {status.channel}")
    if status.output is None:
        typer.echo("Output: UNKNOWN")
    else:
        typer.echo(f"Output: {'ON' if status.output else 'OFF'}")
    if status.source is not None:
        typer.echo(f"Source: {safe_terminal_text(status.source)}")
    typer.echo("No switch command was sent.")


@cloud_auth_app.command("login")
def hopper_cloud_auth_login_command() -> None:
    """Store one Shelly Cloud profile in the native OS credential store."""
    server = typer.prompt("Shelly Cloud server")
    device_id = typer.prompt("Shelly device ID")
    auth_key = typer.prompt(
        "Shelly Cloud authorization key",
        hide_input=True,
        confirmation_prompt=True,
    )
    try:
        shelly_cloud_credentials.store_profile(
            server=server,
            device_id=device_id,
            auth_key=auth_key,
        )
    except ValueError:
        typer.echo("Shelly Cloud login failed: profile values are invalid.", err=True)
        raise typer.Exit(code=1) from None
    except shelly_cloud_credentials.ShellyCloudCredentialError:
        typer.echo("Shelly Cloud login failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Shelly Cloud profile stored in the native OS credential store.")


@cloud_auth_app.command("status")
def hopper_cloud_auth_status_command() -> None:
    """Show whether a Shelly Cloud profile exists without displaying its values."""
    try:
        resolved = shelly_cloud_credentials.resolve_profile()
    except shelly_cloud_credentials.ShellyCloudCredentialError:
        typer.echo(
            "Shelly Cloud authentication status failed: credential store access failed.", err=True
        )
        raise typer.Exit(code=1) from None
    if resolved.profile is None:
        typer.echo("Shelly Cloud profile is not configured.", err=True)
        raise typer.Exit(code=1)
    typer.echo("Shelly Cloud profile is configured in the native OS credential store.")


@cloud_auth_app.command("logout")
def hopper_cloud_auth_logout_command() -> None:
    """Remove the Shelly Cloud profile from the native OS credential store."""
    try:
        deleted = shelly_cloud_credentials.delete_profile()
    except shelly_cloud_credentials.ShellyCloudCredentialError:
        typer.echo("Shelly Cloud logout failed: credential store access failed.", err=True)
        raise typer.Exit(code=1) from None
    if deleted:
        typer.echo("Shelly Cloud profile removed from the native OS credential store.")
    else:
        typer.echo("No stored Shelly Cloud profile was found.")
