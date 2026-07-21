"""
Typer CLI entry: ``ovbuilder`` / ``python -m ovbuilder``.

=============================================================================
DESIGN LANGUAGE (matches ovox CLI)
=============================================================================
* Typer + Rich
* Global callback attaches config to ctx.obj
* No subcommand → default to ``build``
* Version via -V / --version (eager callback)

Subcommands
-----------
  build   — provision a VM (golden clone or ISO)
  config  — dump effective configuration
  version — print version string
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from .build import build as build_command
from .config import get_config_manager
from .version import get_version

console = Console()

# Root Typer app: no_args_is_help=False so bare ``ovbuilder`` runs build.
cli = typer.Typer(
    name="ovbuilder",
    help=(
        "ovbuilder — provision OpenVox-ready VMware VMs "
        "(Packer golden clone or legacy ISO)\n\n"
        "Running with no subcommand defaults to `build`. "
        "Use `ovbuilder --help` for options."
    ),
    add_completion=True,
    rich_markup_mode="rich",
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    """Eager --version handler (exits before subcommand dispatch)."""
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
        help="Path to the Terraform root module (directory containing main.tf)",
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
    Load config into ctx.obj; default to build when no subcommand is given.

    Note: calling build_command(ctx) directly (no subcommand) means Typer
    does not bind Option defaults on build() — build() therefore strips
    OptionInfo sentinels. That is intentional for ``ovbuilder`` ergonomics.
    """
    cfg = get_config_manager().load_config()

    # CLI flag overrides config for this process only (not written to disk).
    if terraform_dir:
        cfg.terraform_dir = terraform_dir

    ctx.obj = {"config": cfg}

    if ctx.invoked_subcommand is None:
        # Bare ``ovbuilder`` → same as ``ovbuilder build``.
        build_command(ctx)


# Register build as both default and explicit subcommand.
cli.command("build", help="Build a VM from a Packer golden template or ISO")(
    build_command
)


@cli.command()
def config(ctx: typer.Context, show: bool = True):
    """Print the effective configuration model (for debugging)."""
    cfg = (
        ctx.obj.get("config")
        if ctx.obj
        else get_config_manager().load_config()
    )
    # model_dump() is plain data (no secrets unless someone put them in YAML).
    console.print(Panel(str(cfg.model_dump()), title="ovbuilder configuration"))


@cli.command("version")
def version_cmd():
    """Show the installed ovbuilder version (same as -V)."""
    console.print(f"ovbuilder {get_version()}")


if __name__ == "__main__":
    cli()
