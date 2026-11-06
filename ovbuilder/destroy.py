"""``ovbuilder destroy`` — remove a VM and retire its per-VM Terraform state.

Takes the same per-hostname lock as ``ovbuilder build``, so it refuses to
run while a build of that hostname is applying. Also cleans up after a
cancelled or failed build: a partly created VM is destroyed, and leftover
state is retired even when the VM never appeared in vCenter.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt

from . import vmops, vmstate, vsphere
from .config import get_config_manager
from .secrets import get_vsphere_password
from .vmstate import StateLockedError

console = Console()


def destroy(
    ctx: typer.Context,
    hostname: str = typer.Argument(..., help="VM name (the hostname it was built with)"),
    vsphere_server: Optional[str] = typer.Option(
        None, "--vsphere-server", envvar="VSPHERE_SERVER", help="vCenter FQDN (default: config)"
    ),
    vsphere_user: Optional[str] = typer.Option(
        None, "--vsphere-user", envvar="VSPHERE_USER", help="vCenter username"
    ),
    datacenter: Optional[str] = typer.Option(
        None, "--datacenter", help="Only look for the VM in this datacenter"
    ),
    keep_state: bool = typer.Option(
        False, "--keep-state", help="Leave the per-VM Terraform state in place"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
):
    """Destroy a VM (and its disks) and retire its Terraform state. Irreversible."""
    cfg = ctx.obj.get("config") if ctx.obj else get_config_manager().load_config()
    server = vsphere_server or cfg.vsphere_server
    user = vsphere_user or Prompt.ask("vSphere username")
    password = get_vsphere_password() or Prompt.ask("vSphere password", password=True)

    if not yes and not Confirm.ask(
        f"[red]Destroy {hostname}[/red] and delete its disks? This cannot be undone.",
        default=False,
    ):
        raise typer.Exit(1)

    try:
        si = vsphere.connect(server, user, password, ignore_ssl=cfg.vsphere_allow_unverified_ssl)
    except Exception as exc:
        console.print(f"[red]vCenter connection failed: {exc}[/red]")
        raise typer.Exit(1)

    try:
        result = vmstate.destroy_with_state(
            si, hostname, datacenter or None, keep_state=keep_state
        )
    except StateLockedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except vmops.VmOpsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    finally:
        vsphere.disconnect(si)

    if result.get("action") == "already_absent":
        console.print(f"[yellow]{hostname} is not in vCenter; leftover state cleaned up.[/yellow]")
    else:
        console.print(f"[green]Destroyed {hostname}.[/green]")
    if result.get("state_retired_to"):
        console.print(f"[dim]Terraform state moved to {result['state_retired_to']}[/dim]")
