"""
`ovbuilder build` command.

Default path: clone a Packer golden template (AlmaLinux 10 / Ubuntu 24.04),
inject hostname + static IP via cloud-init guestinfo, then the VM is ready
for SSH login. OpenVox agent install is a separate step once ACLs allow it.

Legacy path: provision_mode=iso (empty disk + ISO) still available.
"""

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .cloud_init import guestinfo_extra_config
from .config import OvbuilderConfig, get_config_manager
from .network import PrefixParseError, parse_cidr_prefix, prefix_to_netmask
from .ssh import run_post_install_steps
from .terraform import run_terraform_apply
from . import vsphere

console = Console()


def get_known_isos(cfg: OvbuilderConfig):
    items = []
    for label, path in cfg.known_isos.items():
        items.append((label, path))
    items.append(("Other (enter path manually)", None))
    return items


def build(
    ctx: typer.Context,
    hostname: Optional[str] = typer.Option(None, "--hostname", "-H", help="VM hostname / certname"),
    ip: Optional[str] = typer.Option(None, "--ip", help="Desired IP address"),
    os_image: Optional[str] = typer.Option(
        None,
        "--os",
        help="Golden image key (e.g. almalinux-10, ubuntu-24.04)",
    ),
    iso: Optional[str] = typer.Option(None, "--iso", help="ISO path (iso mode only)"),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="golden (default) or iso (legacy empty disk + ISO)",
    ),
    prefix: Optional[str] = typer.Option(
        None,
        "--prefix",
        "--cidr",
        help="IPv4 prefix or netmask (19, /19, 255.255.224.0). Default 24.",
    ),
    cpus: Optional[int] = typer.Option(None, "--cpus", "-c"),
    memory: Optional[int] = typer.Option(None, "--memory", "-m", help="Memory in GB"),
    disk: Optional[int] = typer.Option(None, "--disk", "-d", help="Disk size in GB (thin)"),
    non_interactive: bool = typer.Option(False, "--yes", "-y", help="Do not prompt"),
    vsphere_server: Optional[str] = typer.Option(None, "--vsphere-server", help="vCenter FQDN"),
    vsphere_user: Optional[str] = typer.Option(None, "--vsphere-user", help="vCenter username"),
    vsphere_password: Optional[str] = typer.Option(None, "--vsphere-password", help="vCenter password"),
    gateway: Optional[str] = typer.Option(None, "--gateway", help="Default gateway IP"),
    dns: Optional[str] = typer.Option(None, "--dns", help="DNS server IP"),
):
    """
    Build a VM from a Packer golden template (default) or legacy ISO.

    Interactive: select OS → interview (host/IP/CIDR/…) → Terraform clone.
    """
    try:
        from typer.models import OptionInfo
    except Exception:
        OptionInfo = ()

    def _is_optioninfo(v):
        if isinstance(v, OptionInfo):
            return True
        return type(v).__name__ == "OptionInfo"

    for name in (
        "hostname", "ip", "os_image", "iso", "mode", "prefix", "cpus", "memory",
        "disk", "non_interactive", "vsphere_server", "vsphere_user",
        "vsphere_password", "gateway", "dns",
    ):
        if _is_optioninfo(locals()[name]):
            if name == "non_interactive":
                non_interactive = False
            else:
                locals()  # no-op; assign below
    if _is_optioninfo(hostname):
        hostname = None
    if _is_optioninfo(ip):
        ip = None
    if _is_optioninfo(os_image):
        os_image = None
    if _is_optioninfo(iso):
        iso = None
    if _is_optioninfo(mode):
        mode = None
    if _is_optioninfo(prefix):
        prefix = None
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
    if _is_optioninfo(gateway):
        gateway = None
    if _is_optioninfo(dns):
        dns = None

    cfg: OvbuilderConfig = ctx.obj.get("config") if ctx.obj else get_config_manager().load_config()
    tf_dir = get_config_manager().get_effective_terraform_dir()

    provision_mode = (mode or cfg.provision_mode or "golden").strip().lower()
    if provision_mode in ("clone", "template", "golden"):
        provision_mode = "golden"
    elif provision_mode != "iso":
        console.print(f"[yellow]Unknown mode {provision_mode!r}; using golden[/yellow]")
        provision_mode = "golden"

    # Interactive if missing required identity / OS source
    if provision_mode == "golden":
        interactive = not (hostname and ip and os_image) and not non_interactive
    else:
        interactive = not (hostname and ip and iso) and not non_interactive

    template_name = ""
    guest_id = ""
    default_user = "root"
    iso_path = iso or ""
    os_key = os_image or ""
    prefix_len = 24

    if interactive:
        console.print(
            Panel.fit(
                "[bold]ovbuilder — OpenVox VM Builder[/bold]",
                subtitle="Packer golden clone + interview (or legacy ISO)",
            )
        )

        vsphere_server = Prompt.ask("vSphere server FQDN", default=cfg.vsphere_server)
        vsphere_user = Prompt.ask("vSphere username")
        vsphere_password = Prompt.ask("vSphere password", password=True)

        console.print(f"[dim]Connecting to {vsphere_server} to discover inventory...[/dim]")
        try:
            si = vsphere.connect(vsphere_server, vsphere_user, vsphere_password)
        except Exception as exc:
            console.print(f"[red]vCenter connection failed: {exc}[/red]")
            raise typer.Exit(1)

        dcs = vsphere.list_datacenters(si)
        if not dcs:
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
                dc = dcs[int(choice) - 1]
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
                cluster = clusters[int(choice) - 1]
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
                vm_ds = dss[int(choice) - 1]
            except Exception:
                vm_ds = dss[0]
            if provision_mode == "iso":
                choice = Prompt.ask("Select ISO datastore", default="1")
                try:
                    iso_ds = dss[int(choice) - 1]
                except Exception:
                    iso_ds = dss[0]
            else:
                iso_ds = cfg.iso_datastore

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

        # ── OS source: golden templates or ISO ─────────────────────────
        if provision_mode == "golden":
            images = list(cfg.golden_images.items())
            if not images:
                console.print("[red]No golden_images configured in config.yaml[/red]")
                raise typer.Exit(1)
            table = Table(title="Packer golden images (OS)")
            table.add_column("#", style="cyan")
            table.add_column("Key")
            table.add_column("Template")
            table.add_column("Description")
            for i, (key, gi) in enumerate(images, 1):
                table.add_row(str(i), key, gi.template, gi.description or "")
            console.print(table)
            choice = Prompt.ask("Select OS", default="1")
            try:
                os_key, gi = images[int(choice) - 1]
            except Exception:
                os_key, gi = images[0]
            template_name = gi.template
            guest_id = gi.guest_id
            default_user = gi.default_user
            console.print(
                f"[dim]Will clone template [bold]{template_name}[/bold] "
                f"(login user: {default_user})[/dim]"
            )
        else:
            live_isos = vsphere.list_isos(si, iso_ds, dc) or []
            if live_isos:
                items = [(p, p) for p in live_isos]
                table_title = f"ISO images on {iso_ds}"
            else:
                console.print(
                    f"[yellow]No ISOs discovered on '{iso_ds}'. "
                    "Falling back to known_isos.[/yellow]"
                )
                items = list(get_known_isos(cfg)[:-1])
                table_title = f"Known ISOs (config) — expected on {iso_ds}"
            items.append(("Other (enter path manually)", None))
            table = Table(title=table_title)
            table.add_column("#", style="cyan")
            table.add_column("ISO")
            for i, (label, path) in enumerate(items, 1):
                table.add_row(str(i), path or label)
            console.print(table)
            choice = Prompt.ask("Select ISO", default="1")
            try:
                _, selected = items[int(choice) - 1]
            except Exception:
                selected = items[0][1] if items else None
            if selected is None:
                iso_path = Prompt.ask("Enter ISO path (relative to iso datastore, or full)")
            else:
                iso_path = selected

        vsphere.disconnect(si)

        console.print(f"[dim]Using Terraform module at:[/dim] {tf_dir}")
        cfg.vsphere_server = vsphere_server
        cfg.datacenter = dc
        cfg.cluster = cluster
        cfg.vm_datastore = vm_ds
        cfg.iso_datastore = iso_ds
        cfg.networks = networks

        hostname = Prompt.ask("Hostname")
        ip = Prompt.ask("IP Address")
        console.print(
            "[dim]Subnet (CIDR): 0–32, or dotted netmask. Forms: "
            "[bold]19[/bold], [bold]/19[/bold], [bold]255.255.224.0[/bold][/dim]"
        )
        while True:
            raw_prefix = Prompt.ask(
                "Subnet prefix or netmask (e.g. 24, /19, 255.255.224.0)",
                default="24",
            )
            try:
                prefix_len = parse_cidr_prefix(raw_prefix)
                console.print(
                    f"[dim]Using [bold]/{prefix_len}[/bold] "
                    f"(netmask {prefix_to_netmask(prefix_len)})[/dim]"
                )
                break
            except PrefixParseError as exc:
                console.print(f"[red]{exc}[/red]")
        gateway = Prompt.ask("Default gateway IP (optional)", default="") or None
        dns = Prompt.ask("DNS server IP (optional)", default="") or None

        cpus = int(Prompt.ask("CPUs", default=str(cfg.default_cpus)))
        memory = int(Prompt.ask("Memory (GB)", default=str(cfg.default_memory_gb)))
        disk = int(Prompt.ask("Disk (GB, thin provisioned)", default=str(cfg.default_disk_gb)))

        if not Confirm.ask("Proceed with VM creation?"):
            raise typer.Exit(0)
    else:
        # Non-interactive
        if not hostname or not ip:
            console.print("[red]Non-interactive mode requires --hostname and --ip[/red]")
            raise typer.Exit(1)
        if provision_mode == "golden":
            if not os_image:
                console.print("[red]Golden mode requires --os (e.g. almalinux-10)[/red]")
                raise typer.Exit(1)
            if os_image not in cfg.golden_images:
                console.print(
                    f"[red]Unknown --os {os_image!r}. "
                    f"Configured: {', '.join(cfg.golden_images)}[/red]"
                )
                raise typer.Exit(1)
            gi = cfg.golden_images[os_image]
            os_key = os_image
            template_name = gi.template
            guest_id = gi.guest_id
            default_user = gi.default_user
        else:
            if not iso:
                console.print("[red]ISO mode requires --iso[/red]")
                raise typer.Exit(1)
            iso_path = iso

        try:
            prefix_len = parse_cidr_prefix(prefix if prefix is not None else 24)
        except PrefixParseError as exc:
            console.print(f"[red]Invalid --prefix/--cidr: {exc}[/red]")
            raise typer.Exit(1)
        if cpus is None:
            cpus = cfg.default_cpus
        if memory is None:
            memory = cfg.default_memory_gb
        if disk is None:
            disk = cfg.default_disk_gb
        vsphere_server = vsphere_server or cfg.vsphere_server
        cfg.vsphere_server = vsphere_server

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

    # cloud-init guestinfo for golden clones
    guestinfo = {}
    if provision_mode == "golden":
        guestinfo = guestinfo_extra_config(
            hostname=hostname,
            ip=ip,
            prefix=prefix_len,
            gateway=gateway,
            dns=dns,
            domain=cfg.domain,
        )

    vm_vars = {
        "vm_name": hostname,
        "iso_path": iso_path or "",
        "provision_mode": "clone" if provision_mode == "golden" else "iso",
        "template_name": template_name,
        "guest_id": guest_id,
        "guestinfo_extra_config": guestinfo,
        "num_cpus": cpus,
        "memory_mb": memory * 1024,
        "disk_size_gb": disk,
        "networks": cfg.networks,
        "datacenter": cfg.datacenter,
        "cluster": cfg.cluster,
        "vm_datastore": cfg.vm_datastore,
        "iso_datastore": cfg.iso_datastore,
        "vsphere_server": cfg.vsphere_server,
        "vsphere_user": vsphere_user,
        "vsphere_password": vsphere_password,
    }

    vsphere_user = vm_vars.get("vsphere_user") or vsphere_user
    vsphere_password = vm_vars.get("vsphere_password") or vsphere_password

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

    if provision_mode == "golden":
        console.print(
            Panel.fit(
                f"[bold green]VM '{hostname}' cloned from {template_name}.[/bold green]\n\n"
                f"• Identity (cloud-init): [bold]{hostname}[/bold] @ "
                f"[bold]{ip}/{prefix_len}[/bold]\n"
                f"• SSH when Tools/network settle: "
                f"[bold]{default_user}@{ip}[/bold]\n"
                f"• OpenVox agent (after ACLs allow {cfg.openvox_server}:8140):\n"
                f"  curl -k --noproxy {cfg.openvox_server} "
                f"https://{cfg.openvox_server}:8140/packages/install.bash | sudo bash\n\n"
                "[dim]No console OS install required.[/dim]",
                title="Golden clone complete",
            )
        )
        if non_interactive:
            return
        if Confirm.ask(
            "Run OpenVox agent bootstrap over SSH now? "
            "(only if ACLs already allow access to the server)",
            default=False,
        ):
            ssh_user = Prompt.ask("SSH username", default=default_user)
            ssh_pass = Prompt.ask("SSH password", password=True)
            ok = run_post_install_steps(
                ip=ip,
                username=ssh_user,
                password=ssh_pass,
                hostname=hostname,
                prefix=prefix_len,
                gateway=gateway,
                dns=dns,
                openvox_server=cfg.openvox_server,
            )
            if ok:
                console.print(
                    Panel.fit(
                        "[bold green]Agent bootstrap finished.[/bold green]",
                        title="Done",
                    )
                )
            else:
                console.print("[red]Bootstrap had errors; check output above.[/red]")
                raise typer.Exit(1)
        return

    # Legacy ISO path messaging
    console.print(
        Panel.fit(
            f"[bold green]VM '{hostname}' created (ISO mode).[/bold green]\n\n"
            "1. Connect to the VM console in vSphere.\n"
            "2. Complete the OS installation.\n"
            f"3. Set hostname to [bold]{hostname}[/bold] and IP "
            f"[bold]{ip}/{prefix_len}[/bold].\n"
            "4. Ensure a sudo-capable user exists.",
            title="Manual OS Installation Required",
        )
    )

    if non_interactive:
        console.print("[yellow]Non-interactive: skipping post-install SSH.[/yellow]")
        return

    if not Confirm.ask("Has the OS been installed and can you SSH to the new IP?"):
        console.print("[yellow]Exiting. Re-run later or SSH manually.[/yellow]")
        return

    ssh_user = Prompt.ask("SSH username with sudo rights", default="root")
    ssh_pass = Prompt.ask("SSH password", password=True)
    ok = run_post_install_steps(
        ip=ip,
        username=ssh_user,
        password=ssh_pass,
        hostname=hostname,
        prefix=prefix_len,
        gateway=gateway,
        dns=dns,
        openvox_server=cfg.openvox_server,
    )
    if ok:
        console.print(
            Panel.fit(
                "[bold green]Success![/bold green]\nNode should register with OpenVox.",
                title="Done",
            )
        )
    else:
        console.print("[red]Post-install step encountered errors.[/red]")
        raise typer.Exit(1)
