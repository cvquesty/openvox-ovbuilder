"""
`ovbuilder build` command.

This is the core of the tool. It follows the interactive + flag-driven
style used elsewhere in OpenVox tooling.
"""

from pathlib import Path
from typing import Optional

import paramiko
import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import get_config_manager, OvbuilderConfig
from .terraform import run_terraform_apply
from .ssh import run_post_install_steps

console = Console()


def get_known_isos(cfg: OvbuilderConfig):
    """Return a list of (label, path) for the OS selector."""
    items = []
    for label, path in cfg.known_isos.items():
        items.append((label, path))
    items.append(("Other (enter path manually)", None))
    return items


def build(
    ctx: typer.Context,
    # Non-interactive / flag mode
    hostname: Optional[str] = typer.Option(None, "--hostname", "-H", help="VM hostname / certname"),
    ip: Optional[str] = typer.Option(None, "--ip", help="Desired IP address"),
    iso: Optional[str] = typer.Option(None, "--iso", help="Path to ISO on the ISO datastore"),
    cpus: Optional[int] = typer.Option(None, "--cpus", "-c"),
    memory: Optional[int] = typer.Option(None, "--memory", "-m", help="Memory in GB"),
    disk: Optional[int] = typer.Option(None, "--disk", "-d", help="Disk size in GB (thin)"),
    non_interactive: bool = typer.Option(False, "--yes", "-y", help="Do not prompt; fail if required values missing"),
):
    """
    Build a new VMware VM from an ISO and register it with OpenVox.

    When run with no arguments (or missing critical flags), ovbuilder
    enters an interactive mode with a friendly OS selector and prompts.
    """
    cfg: OvbuilderConfig = ctx.obj.get("config") if ctx.obj else get_config_manager().load_config()
    tf_dir = get_config_manager().get_effective_terraform_dir()

    # Collect parameters (interactive if needed)
    interactive = not (hostname and ip and iso) and not non_interactive

    vsphere_user = None
    vsphere_password = None

    if interactive:
        console.print(Panel.fit("[bold]ovbuilder — OpenVox VM Builder[/bold]", subtitle="Terraform + ISO + Agent registration"))

        # 1. Collect vSphere credentials (like "login" in ovox)
        vsphere_user = Prompt.ask("vSphere username")
        vsphere_password = Prompt.ask("vSphere password", password=True)

        console.print(f"[dim]Using Terraform module at:[/dim] {tf_dir}")

        # 2. OS Selector
        isos = get_known_isos(cfg)
        table = Table(title="Available OS Images")
        table.add_column("#", style="cyan")
        table.add_column("OS")
        table.add_column("ISO Path")

        for i, (label, path) in enumerate(isos, 1):
            table.add_row(str(i), label, path or "(manual)")

        console.print(table)
        choice = Prompt.ask("Select OS", default="1")
        try:
            idx = int(choice) - 1
            selected_label, selected_iso = isos[idx]
        except Exception:
            selected_label, selected_iso = isos[-1]

        if selected_iso is None:
            iso_path = Prompt.ask("Enter ISO path (relative to iso_datastore)")
        else:
            iso_path = selected_iso

        # 3. Identity
        hostname = Prompt.ask("Hostname")
        ip = Prompt.ask("IP Address")
        prefix = int(Prompt.ask("Prefix (CIDR)", default="24"))
        gateway = Prompt.ask("Gateway (optional)", default="") or None
        dns = Prompt.ask("DNS (optional)", default="") or None

        # 4+5. Sizing (thin is always on in the module)
        cpus = int(Prompt.ask("CPUs", default=str(cfg.default_cpus)))
        memory = int(Prompt.ask("Memory (GB)", default=str(cfg.default_memory_gb)))
        disk = int(Prompt.ask("Disk (GB, thin provisioned)", default=str(cfg.default_disk_gb)))

        if not Confirm.ask("Proceed with VM creation?"):
            raise typer.Exit(0)
    else:
        # Non-interactive path
        if not (hostname and ip and iso):
            console.print("[red]In non-interactive mode you must supply --hostname, --ip, and --iso[/red]")
            raise typer.Exit(1)
        iso_path = iso
        prefix = 24
        gateway = None
        dns = None
        if cpus is None: cpus = cfg.default_cpus
        if memory is None: memory = cfg.default_memory_gb
        if disk is None: disk = cfg.default_disk_gb

    # Build variable dict for our itsys module
    vm_vars = {
        "vm_name": hostname,
        "iso_path": iso_path,
        "num_cpus": cpus,
        "memory_mb": memory * 1024,
        "disk_size_gb": disk,
        "networks": cfg.networks,
        # guest_id will be inferred in the Terraform or we can extend later
    }

    # Run Terraform
    success = run_terraform_apply(
        tf_dir=tf_dir,
        vars=vm_vars,
        config=cfg,
        vsphere_user=vsphere_user,
        vsphere_password=vsphere_password,
    )

    if not success:
        console.print("[red]Terraform provisioning failed.[/red]")
        raise typer.Exit(1)

    console.print(
        Panel.fit(
            f"[bold green]VM '{hostname}' created.[/bold green]\n\n"
            "1. Connect to the VM console in vSphere.\n"
            "2. Complete the OS installation.\n"
            f"3. Set hostname to [bold]{hostname}[/bold] and configure IP [bold]{ip}/{prefix}[/bold].\n"
            "4. Ensure a sudo-capable user exists (root is fine for the bootstrap step).",
            title="Manual OS Installation Required"
        )
    )

    if non_interactive:
        console.print("[yellow]Non-interactive mode: skipping post-install SSH step.[/yellow]")
        console.print(f"Manually run on the VM when ready:\n  curl -k --noproxy {cfg.openvox_server} https://{cfg.openvox_server}:8140/packages/install.bash | sudo bash")
        return

    if not Confirm.ask("Has the OS been installed and can you SSH to the new IP?"):
        console.print("[yellow]Exiting. You can re-run 'ovbuilder build' or SSH manually later.[/yellow]")
        return

    ssh_user = Prompt.ask("SSH username with sudo rights", default="root")
    ssh_pass = Prompt.ask("SSH password", password=True)

    # Run the post-install steps (network config + OpenVox agent)
    ok = run_post_install_steps(
        ip=ip,
        username=ssh_user,
        password=ssh_pass,
        hostname=hostname,
        prefix=prefix,
        gateway=gateway,
        dns=dns,
        openvox_server=cfg.openvox_server,
    )

    if ok:
        console.print(Panel.fit("[bold green]Success![/bold green]\nThe node should now be registering with OpenVox.", title="Done"))
    else:
        console.print("[red]Post-install step encountered errors. Check the output above.[/red]")
        raise typer.Exit(1)
