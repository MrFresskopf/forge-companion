"""Command-line interface for Forge Companion."""

import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from time import sleep as sleep_seconds
from typing import Annotated, Any
from uuid import UUID

import httpx
import typer

from forge_companion import (
    __version__,
    credentials,
    preferences,
    shelly_cloud_credentials,
)
from forge_companion.backup import (
    SnapshotValidationError,
    create_backup,
    validate_backup_file,
    write_backup,
)
from forge_companion.client import BrewForgeClient
from forge_companion.diagnostics import run_doctor
from forge_companion.doctor_output import (
    DoctorSetupErrorCode,
    build_doctor_document,
    render_doctor_json,
)
from forge_companion.fermentation import analyze_readings, parse_readings
from forge_companion.fermentation_csv import render_csv, write_csv
from forge_companion.fermentation_html import render_html, write_html
from forge_companion.fermentation_report import render_markdown, write_markdown
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
from forge_companion.spunding_advisor import AdvisorConfig, advise_spunding_payload
from forge_companion.spunding_report import render_spunding_advice
from forge_companion.terminal_text import safe_terminal_text

app = typer.Typer(
    help=(
        "Safe Shelly control and guarded brewery automation with optional "
        "read-only BrewForge reports."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
)
auth_app = typer.Typer(
    help="Manage BrewForge authentication without displaying tokens.",
    no_args_is_help=False,
    invoke_without_command=True,
)
snapshot_app = typer.Typer(
    help="Run without a subcommand to create; use snapshot validate to verify offline.",
    invoke_without_command=True,
)
hopper_app = typer.Typer(
    help="Prepare, rehearse, and fire guarded remote-hopper plans.",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(hopper_app, name="hopper", rich_help_panel="Start here")
app.add_typer(auth_app, name="auth", rich_help_panel="Supporting BrewForge")
app.add_typer(snapshot_app, name="snapshot", rich_help_panel="Supporting BrewForge")
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


def _is_interactive_terminal() -> bool:
    """Require a human-attended terminal for live hardware confirmation."""
    return sys.stdin.isatty() and sys.stdout.isatty()


@app.callback()
def main(
    context: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the version and exit.", is_eager=True),
    ] = False,
) -> None:
    """Run safe Shelly control and read-only brewing companion commands."""
    if version:
        typer.echo(f"Forge Companion {__version__}")
        raise typer.Exit()
    if context.invoked_subcommand is None:
        typer.echo("Forge Companion\n")
        typer.echo("Start with safe Shelly planning and read-only status checks:")
        typer.echo("  forge-companion hopper --help")
        typer.echo("\nMore tools: forge-companion --help")


@auth_app.callback()
def auth_command(context: typer.Context) -> None:
    """Manage BrewForge authentication without displaying tokens."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())


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


def _authentication_failed(error: credentials.CredentialStoreError) -> None:
    if isinstance(error, credentials.InvalidEnvironmentCredentialError):
        message = "Authentication failed: BREWFORGE_API_TOKEN is invalid."
    elif isinstance(error, credentials.InvalidStoredCredentialError):
        message = (
            "Authentication failed: stored credential is invalid; "
            "run `forge-companion auth logout` and log in again."
        )
    else:
        message = "Authentication failed: native credential store access failed."
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _report_environment_state(active_message: str) -> None:
    status = credentials.environment_override_status()
    if status == "valid":
        typer.echo(active_message)
    elif status == "invalid":
        typer.echo("BREWFORGE_API_TOKEN is set but invalid and prevents stored credential use.")


def _token_for_api() -> str:
    try:
        resolved = credentials.resolve_token()
    except credentials.CredentialStoreError as error:
        _authentication_failed(error)
    if resolved.token is None:
        typer.echo(
            "Error: BREWFORGE_API_TOKEN is not set and no stored credential was found; "
            "run `forge-companion auth login`.",
            err=True,
        )
        raise typer.Exit(code=2)
    return resolved.token


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


@auth_app.command("login")
def auth_login_command() -> None:
    """Store a BrewForge API token in the native OS credential store."""
    token = typer.prompt(
        "BrewForge API token",
        hide_input=True,
        confirmation_prompt=True,
    )
    try:
        credentials.store_token(token)
    except ValueError:
        typer.echo(
            "Authentication failed: token must not be empty or contain whitespace.", err=True
        )
        raise typer.Exit(code=1) from None
    except credentials.CredentialStoreError as error:
        _authentication_failed(error)
    typer.echo("Credential stored in the native OS credential store.")
    _report_environment_state("BREWFORGE_API_TOKEN currently overrides the stored credential.")


@auth_app.command("status")
def auth_status_command() -> None:
    """Show the active authentication source without displaying a token."""
    try:
        resolved = credentials.resolve_token()
    except credentials.CredentialStoreError as error:
        _authentication_failed(error)
    if resolved.source == "environment":
        typer.echo("Authentication source: BREWFORGE_API_TOKEN environment override.")
    elif resolved.source == "keyring":
        typer.echo("Authentication source: native OS credential store.")
    else:
        typer.echo("Authentication is not configured.", err=True)
        raise typer.Exit(code=1)


@auth_app.command("logout")
def auth_logout_command() -> None:
    """Delete the native stored credential without changing the environment."""
    try:
        deleted = credentials.delete_token()
    except credentials.CredentialStoreError as error:
        _authentication_failed(error)
    if deleted:
        typer.echo("Stored credential deleted.")
    else:
        typer.echo("No stored credential was present.")
    _report_environment_state("BREWFORGE_API_TOKEN remains active and was not changed.")


def _echo_doctor_json(document: dict[str, object]) -> None:
    typer.echo(render_doctor_json(document))


def _doctor_credential_error_code(
    error: credentials.CredentialStoreError,
) -> DoctorSetupErrorCode:
    if isinstance(error, credentials.InvalidEnvironmentCredentialError):
        return "invalid_environment_credential"
    if isinstance(error, credentials.InvalidStoredCredentialError):
        return "invalid_stored_credential"
    return "credential_store_error"


@app.command(rich_help_panel="Supporting BrewForge")
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the versioned machine-readable result."),
    ] = False,
) -> None:
    """Check authentication and documented read-only API collections."""
    if json_output:
        try:
            resolved = credentials.resolve_token()
        except credentials.CredentialStoreError as error:
            _echo_doctor_json(
                build_doctor_document([], error_code=_doctor_credential_error_code(error))
            )
            raise typer.Exit(code=1) from None
        if resolved.token is None:
            _echo_doctor_json(build_doctor_document([], error_code="authentication_required"))
            raise typer.Exit(code=2)
        token = resolved.token
    else:
        token = _token_for_api()
    try:
        client = BrewForgeClient(token=token)
    except (httpx.InvalidURL, ImportError, OSError, ValueError):
        if json_output:
            _echo_doctor_json(build_doctor_document([], error_code="client_setup_error"))
            raise typer.Exit(code=1) from None
        raise
    checks = run_doctor(client)
    if json_output:
        _echo_doctor_json(build_doctor_document(checks))
        if any(not check.ok for check in checks):
            raise typer.Exit(code=1)
        return
    for check in checks:
        marker = "OK" if check.ok else "FAIL"
        detail = str(check.status) if check.status is not None else check.error or "unknown error"
        typer.echo(f"{marker:4} {check.path:28} {detail}")
    if any(not check.ok for check in checks):
        raise typer.Exit(code=1)


@snapshot_app.callback()
def snapshot_command(
    context: typer.Context,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination JSON file."),
    ] = Path("snapshots/brewforge-collections.json"),
) -> None:
    """Create a local snapshot of supported BrewForge API collections."""
    if context.invoked_subcommand is not None:
        return
    client = BrewForgeClient(token=_token_for_api())
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


@snapshot_app.command("validate")
def snapshot_validate_command(
    source: Annotated[
        Path,
        typer.Argument(help="Collection snapshot JSON file."),
    ] = Path("snapshots/brewforge-collections.json"),
) -> None:
    """Validate snapshot schema and integrity without contacting BrewForge."""
    try:
        summary = validate_backup_file(source)
    except SnapshotValidationError:
        typer.echo("Snapshot validation failed: file is invalid or unreadable.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Snapshot is valid.")
    typer.echo(f"Format: {summary.format}")
    typer.echo(f"Created: {summary.created_at}")
    typer.echo(f"Generator: Forge Companion {summary.generator_version}")
    typer.echo(f"Collections: {summary.collection_count}")
    typer.echo(f"Records: {summary.record_count}")
    typer.echo("SHA-256 integrity: verified.")
    typer.echo("Excluded: brew details, brew notes, brew readings, undocumented resources.")


@app.command("fermentation-brief", hidden=True)
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


@app.command("fermentation-csv", hidden=True)
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


@app.command("fermentation-html", hidden=True)
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


@app.command("report", rich_help_panel="Supporting BrewForge")
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


@app.command("spunding-advisor", rich_help_panel="Safety experiments")
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


@app.command("brews", hidden=True)
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
