"""Command-line composition root for Forge Companion."""

from typing import Annotated

import typer

from forge_companion import __version__
from forge_companion.cli_brewforge import (
    auth_app,
    snapshot_app,
)
from forge_companion.cli_brewforge import (
    register_root_commands as register_brewforge_commands,
)
from forge_companion.cli_hopper import hopper_app
from forge_companion.cli_reports import register_root_commands as register_report_commands

app = typer.Typer(
    help=(
        "Safe Shelly control and guarded brewery automation with optional "
        "read-only BrewForge reports."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(hopper_app, name="hopper", rich_help_panel="Start here")
app.add_typer(auth_app, name="auth", rich_help_panel="Supporting BrewForge")
app.add_typer(snapshot_app, name="snapshot", rich_help_panel="Supporting BrewForge")
register_brewforge_commands(app)
register_report_commands(app)


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
