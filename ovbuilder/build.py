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
from . import vsphere

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
    vsphere_server: Optional[str] = typer.Option(None, "--vsphere-server", help="vCenter FQDN"),
    vsphere_user: Optional[str] = typer.Option(None, "--vsphere-user", help="vCenter username"),
    vsphere_password: Optional[str] = typer.Option(None, "--vsphere-password", help="vCenter password"),
):
    """
    Build a new VMware VM from an ISO and register it with OpenVox.

    When run with no arguments (or missing critical flags), ovbuilder
    enters an interactive mode with a friendly OS selector and prompts.
    """
    # When the bare "ovbuilder" command (no subcommand) invokes us directly via
    # build_command(ctx), Typer has not processed the Option() defaults.
    # The parameters arrive as OptionInfo objects instead of their resolved
    # values (None, etc.). Guard against that so the rest of the logic works.
    try:
        from typer.models import OptionInfo
    except Exception:
        OptionInfo = ()

    def _is_optioninfo(v):
        if isinstance(v, OptionInfo):
            return True
        return type(v).__name__ == "OptionInfo"

    if _is_optioninfo(hostname):
        hostname = None
    if _is_optioninfo(ip):
        ip = None
    if _is_optioninfo(iso):
        iso = None
    if _is_optioninfo(cpus):
        cpus = None
    if _is_optioninfo(memory):
        memory = None
    if _is_optioninfo(disk):
        disk = None
    if _is_optioninfo(non_interactive):
        non_interactive = False
    if _is_optioninfo(vsphere_server):
        vsphere_server = None
    if _is_optioninfo(vsphere_user):
        vsphere_user = None
    if _is_optioninfo(vsphere_password):
        vsphere_password = None

    cfg: OvbuilderConfig = ctx.obj.get("config") if ctx.obj else get_config_manager().load_config()
    tf_dir = get_config_manager().get_effective_terraform_dir()

    # Collect parameters (interactive if needed)
    interactive = not (hostname and ip and iso) and not non_interactive

    if interactive:
        console.print(Panel.fit("[bold]ovbuilder — OpenVox VM Builder[/bold]", subtitle="Terraform + ISO + Agent registration"))

        # 1. Collect vSphere credentials
        vsphere_server = Prompt.ask("vSphere server FQDN", default=cfg.vsphere_server)
        vsphere_user = Prompt.ask("vSphere username")
        vsphere_password = Prompt.ask("vSphere password", password=True)

        console.print(f"[dim]Connecting to {vsphere_server} to discover inventory...[/dim]")

        try:
            si = vsphere.connect(vsphere_server, vsphere_user, vsphere_password)
        except Exception as exc:
            console.print(f"[red]vCenter connection failed: {exc}[/red]")
            raise typer.Exit(1)

        # Discover real values from vCenter
        dcs = vsphere.list_datacenters(si)
        if not dcs:
            console.print("[yellow]No datacenters found. Falling back to manual entry.[/yellow]")
            dc = Prompt.ask("Datacenter", default=cfg.datacenter)
        else:
            table = Table(title="Datacenters")
            table.add_column("#")
            table.add_column("Name")
            for i, name in enumerate(dcs, 1):
                table.add_row(str(i), name)
            console.print(table)
            choice = Prompt.ask("Select datacenter", default="1")
            try:
                dc = dcs[int(choice)-1]
            except Exception:
                dc = dcs[0]

        clusters = vsphere.list_clusters(si, dc)
        if not clusters:
            cluster = Prompt.ask("Cluster", default=cfg.cluster)
        else:
            table = Table(title=f"Clusters in {dc}")
            table.add_column("#")
            table.add_column("Name")
            for i, name in enumerate(clusters, 1):
                table.add_row(str(i), name)
            console.print(table)
            choice = Prompt.ask("Select cluster", default="1")
            try:
                cluster = clusters[int(choice)-1]
            except Exception:
                cluster = clusters[0]

        dss = vsphere.list_datastores(si, dc)
        if not dss:
            vm_ds = Prompt.ask("VM Datastore", default=cfg.vm_datastore)
            iso_ds = Prompt.ask("ISO Datastore", default=cfg.iso_datastore)
        else:
            table = Table(title=f"Datastores in {dc}")
            table.add_column("#")
            table.add_column("Name")
            for i, name in enumerate(dss, 1):
                table.add_row(str(i), name)
            console.print(table)
            choice = Prompt.ask("Select VM datastore", default="1")
            try:
                vm_ds = dss[int(choice)-1]
            except Exception:
                vm_ds = dss[0]
            choice = Prompt.ask("Select ISO datastore", default="1")
            try:
                iso_ds = dss[int(choice)-1]
            except Exception:
                iso_ds = dss[0]

        nets = vsphere.list_networks(si, dc)
        if not nets:
            net_str = Prompt.ask("Networks (comma separated)", default=",".join(cfg.networks))
            networks = [n.strip() for n in net_str.split(",") if n.strip()]
        else:
            table = Table(title=f"Networks in {dc}")
            table.add_column("#")
            table.add_column("Name")
            for i, name in enumerate(nets, 1):
                table.add_row(str(i), name)
            console.print(table)
            net_str = Prompt.ask("Select networks (comma separated numbers or names)", default="1")
            try:
                sel = [x.strip() for x in net_str.split(",")]
                networks = []
                for s in sel:
                    if s.isdigit():
                        idx = int(s) - 1
                        if 0 <= idx < len(nets):
                            networks.append(nets[idx])
                    elif s in nets:
                        networks.append(s)
                if not networks:
                    networks = [nets[0]]
            except Exception:
                networks = [nets[0]]

        # ISOs from chosen iso datastore - always nice table + manual option, same as other selections
        live_isos = vsphere.list_isos(si, iso_ds, dc) or []
        items = [(p, p) for p in live_isos] + [("Other (enter path manually)", None)]
        table = Table(title=f"ISO images on {iso_ds}")
        table.add_column("#", style="cyan")
        table.add_column("ISO")
        for i, (label, _) in enumerate(items, 1):
            table.add_row(str(i), label)
        console.print(table)
        choice = Prompt.ask("Select ISO", default="1")
        try:
            idx = int(choice) - 1
            selected_label, selected = items[idx]
        except Exception:
            selected = items[0][1] if items else None
        if selected is None:
            iso_path = Prompt.ask("Enter ISO path (relative to iso datastore, or full)")
        else:
            iso_path = selected

        vsphere.disconnect(si)

        console.print(f"[dim]Using Terraform module at:[/dim] {tf_dir}")

        # Update cfg with discovered values for terraform
        cfg.vsphere_server = vsphere_server
        cfg.datacenter = dc
        cfg.cluster = cluster
        cfg.vm_datastore = vm_ds
        cfg.iso_datastore = iso_ds
        cfg.networks = networks

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
        vsphere_server = vsphere_server or cfg.vsphere_server
        cfg.vsphere_server = vsphere_server
        # user/pass may be None, terraform will use env or tfvars if needed

    # Final normalization for sizing values.
    # This makes OptionInfo leakage (from bare "ovbuilder" direct ctx call)
    # or stale installs completely impossible to reach the arithmetic below.
    def _coerce_sizing(val, default):
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            try:
                return int(val)
            except Exception:
                pass
        try:
            from typer.models import OptionInfo
            if isinstance(val, OptionInfo) or type(val).__name__ == "OptionInfo":
                return int(default)
        except Exception:
            pass
        if val is None:
            return int(default)
        try:
            return int(val)
        except Exception:
            return int(default)

    cpus = _coerce_sizing(cpus, cfg.default_cpus)
    memory = _coerce_sizing(memory, cfg.default_memory_gb)
    disk = _coerce_sizing(disk, cfg.default_disk_gb)

    # Build variable dict for the terraform module.
    # Infrastructure values come from discovery (or cfg fallbacks).
    vm_vars = {
        "vm_name": hostname,
        "iso_path": iso_path,
        "num_cpus": cpus,
        "memory_mb": memory * 1024,
        "disk_size_gb": disk,
        "networks": cfg.networks,
        "datacenter": cfg.datacenter,
        "cluster": cfg.cluster,
        "vm_datastore": cfg.vm_datastore,
        "iso_datastore": cfg.iso_datastore,
        "vsphere_server": cfg.vsphere_server,
        # Auth values are also carried here (in addition to the dedicated params below).
        # This makes it harder to accidentally drop them in the long interactive flow.
        "vsphere_user": vsphere_user,
        "vsphere_password": vsphere_password,
    }

    # Final defensive carry of the auth values the user entered (or supplied via flags).
    # The interactive flow is long; this ensures nothing between the prompt and here drops them.
    vsphere_user = vm_vars.get("vsphere_user") or vsphere_user
    vsphere_password = vm_vars.get("vsphere_password") or vsphere_password

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
