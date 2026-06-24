"""
ovbuilder main entry point — Typer CLI application.

Follows the design language of the ovox CLI:
- Typer + rich
- Global callback for options
- XDG config + env var support
- Rich console output with ✓ / ✗
- Subcommand groups where it makes sense
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from . import __version__
from .build import build as build_command
from .config import get_config_manager
from .version import get_version

console = Console()

cli = typer.Typer(
    name="ovbuilder",
    help="ovbuilder — provision OpenVox-ready VMware VMs from ISO images\n\n"
         "Running with no subcommand defaults to `build`. Use `ovbuilder --help` for options.",
    add_completion=True,
    rich_markup_mode="rich",
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"ovbuilder {get_version()}")
        raise typer.Exit()


@cli.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    terraform_dir: Optional[str] = typer.Option(
        None,
        "--terraform-dir",
        "-d",
        envvar="OVBUILDER_TERRAFORM_DIR",
        help="Path to the Terraform root module (itsys/ directory containing main.tf)",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show ovbuilder version and exit",
    ),
):
    """
    ovbuilder builds virtual machines using Terraform + datastore ISOs
    and automatically registers them with OpenVox.

    It is designed to be fast and usable both from a laptop and from
    systems inside the data center.

    Running `ovbuilder` with no subcommand defaults to `ovbuilder build`.
    """
    cfg = get_config_manager().load_config()

    if terraform_dir:
        cfg.terraform_dir = terraform_dir

    ctx.obj = {"config": cfg}

    if ctx.invoked_subcommand is None:
        ctx.invoke(build_command)


cli.command("build", help="Interactively or non-interactively build a new VM")(build_command)


@cli.command()
def config(ctx: typer.Context, show: bool = True):
    """Show the current ovbuilder configuration."""
    cfg = ctx.obj.get("config") if ctx.obj else get_config_manager().load_config()
    console.print(Panel(str(cfg.model_dump()), title="ovbuilder configuration"))


@cli.command()
def version():
    """Show the installed ovbuilder version."""
    console.print(f"ovbuilder {get_version()}")


if __name__ == "__main__":
    cli()
