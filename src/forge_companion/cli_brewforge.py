"""Typer commands for BrewForge authentication, diagnostics, and snapshots."""

from pathlib import Path
from typing import Annotated

import httpx
import typer

from forge_companion import (
    credentials,
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

auth_app = typer.Typer(
    help="Manage BrewForge authentication without displaying tokens.",
    no_args_is_help=False,
    invoke_without_command=True,
)
snapshot_app = typer.Typer(
    help="Run without a subcommand to create; use snapshot validate to verify offline.",
    invoke_without_command=True,
)

@auth_app.callback()
def auth_command(context: typer.Context) -> None:
    """Manage BrewForge authentication without displaying tokens."""
    if context.invoked_subcommand is None:
        typer.echo(context.get_help())
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

def register_root_commands(root_app: typer.Typer) -> None:
    """Register BrewForge diagnostics without changing their root command paths."""
    root_app.command(rich_help_panel="Supporting BrewForge")(doctor)
