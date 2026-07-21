"""
``ovbuilder build`` — interactive / flag-driven VM provisioning.

=============================================================================
END-TO-END FLOWS
=============================================================================

Golden (default)
  1. Discover vSphere inventory (optional interactive menus).
  2. Select Packer golden OS key (almalinux-10 / ubuntu-24.04).
  3. Interview: hostname, IP, CIDR, gateway, DNS, sizing.
  4. Terraform clones template + injects cloud-init guestinfo.
  5. Disconnect any leftover CD media (ISO lock hygiene).
  6. VM ready for SSH; optional agent bootstrap after ACLs.

ISO (legacy)
  1–3 similar, but pick a datastore ISO instead of a template.
  4. Terraform creates empty disk + attaches ISO.
  5. Operator completes OS install in console.
  6. ovbuilder disconnects ISO (releases datastore lock) before reboot.
  7. Optional SSH network + agent bootstrap.

=============================================================================
TYPER / OptionInfo NOTE
=============================================================================
Bare ``ovbuilder`` calls build(ctx) without Typer binding defaults, so
parameters may arrive as typer.models.OptionInfo. We normalize those to
None / False before any logic. Do not remove that guard.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import vsphere
from .cloud_init import guestinfo_extra_config
from .config import OvbuilderConfig, get_config_manager
from .network import PrefixParseError, parse_cidr_prefix, prefix_to_netmask
from .ssh import run_post_install_steps
from .terraform import run_terraform_apply

console = Console()


# ---------------------------------------------------------------------------
# Small helpers (keep build() readable)
# ---------------------------------------------------------------------------

def get_known_isos(cfg: OvbuilderConfig) -> List[Tuple[str, Optional[str]]]:
    """
    Build (label, path) rows for legacy ISO mode menus.

    Appends a final "Other" row with path=None so the operator can type a path.
    """
    items: List[Tuple[str, Optional[str]]] = [
        (label, path) for label, path in cfg.known_isos.items()
    ]
    items.append(("Other (enter path manually)", None))
    return items


def _is_optioninfo(value: Any) -> bool:
    """
    True if Typer passed an unbound Option() sentinel instead of a real value.

    Happens when main() invokes build_command(ctx) without going through
    Typer's parameter binding for the build subcommand.
    """
    try:
        from typer.models import OptionInfo
    except Exception:
        return type(value).__name__ == "OptionInfo"
    return isinstance(value, OptionInfo) or type(value).__name__ == "OptionInfo"


def _coerce_sizing(val: Any, default: int) -> int:
    """
    Coerce CPU/memory/disk to int; OptionInfo / garbage → default.

    Prevents ``TypeError: OptionInfo * 1024`` on bare-ovbuilder invokes.
    """
    if isinstance(val, bool):
        # bool is int subclass; reject nonsense sizing.
        return int(default)
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return int(default)
    if _is_optioninfo(val) or val is None:
        return int(default)
    try:
        return int(val)
    except (TypeError, ValueError):
        return int(default)


def _normalize_provision_mode(mode: Optional[str], cfg_mode: str) -> str:
    """
    Map CLI/config aliases to internal golden | iso.

    Accepts clone/template/golden → golden; unknown → golden with warning.
    """
    provision_mode = (mode or cfg_mode or "golden").strip().lower()
    if provision_mode in ("clone", "template", "golden"):
        return "golden"
    if provision_mode == "iso":
        return "iso"
    console.print(
        f"[yellow]Unknown mode {provision_mode!r}; using golden[/yellow]"
    )
    return "golden"


def _prompt_table_choice(
    title: str,
    names: List[str],
    default_index: int = 0,
) -> str:
    """
    Show a numbered Rich table and return the selected name.

    On parse failure, returns names[default_index] (or empty string if none).
    """
    if not names:
        return ""
    table = Table(title=title)
    table.add_column("#")
    table.add_column("Name")
    for i, name in enumerate(names, 1):
        table.add_row(str(i), name)
    console.print(table)
    choice = Prompt.ask("Select", default=str(default_index + 1))
    try:
        return names[int(choice) - 1]
    except Exception:
        return names[default_index]


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

def build(
    ctx: typer.Context,
    hostname: Optional[str] = typer.Option(
        None, "--hostname", "-H", help="VM hostname / certname"
    ),
    ip: Optional[str] = typer.Option(None, "--ip", help="Desired IP address"),
    os_image: Optional[str] = typer.Option(
        None,
        "--os",
        help="Golden image key (e.g. almalinux-10, ubuntu-24.04)",
    ),
    iso: Optional[str] = typer.Option(
        None, "--iso", help="ISO path (iso mode only)"
    ),
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
    memory: Optional[int] = typer.Option(
        None, "--memory", "-m", help="Memory in GB"
    ),
    disk: Optional[int] = typer.Option(
        None, "--disk", help="Disk size in GB (thin)"
    ),
    non_interactive: bool = typer.Option(
        False, "--yes", "-y", help="Do not prompt"
    ),
    vsphere_server: Optional[str] = typer.Option(
        None, "--vsphere-server", help="vCenter FQDN"
    ),
    vsphere_user: Optional[str] = typer.Option(
        None, "--vsphere-user", help="vCenter username"
    ),
    vsphere_password: Optional[str] = typer.Option(
        None, "--vsphere-password", help="vCenter password"
    ),
    gateway: Optional[str] = typer.Option(
        None, "--gateway", help="Default gateway IP"
    ),
    dns: Optional[str] = typer.Option(None, "--dns", help="DNS server IP"),
):
    """
    Build a VM from a Packer golden template (default) or legacy ISO.

    Interactive: select OS → interview (host/IP/CIDR/…) → Terraform apply.
    """
    # --- Strip OptionInfo sentinels from bare ``ovbuilder`` invoke ----------
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

    # --- Config + terraform path --------------------------------------------
    cfg: OvbuilderConfig = (
        ctx.obj.get("config") if ctx.obj else get_config_manager().load_config()
    )
    tf_dir = get_config_manager().get_effective_terraform_dir()
    provision_mode = _normalize_provision_mode(mode, cfg.provision_mode)

    # Interactive when required identity pieces are missing (unless -y).
    if provision_mode == "golden":
        interactive = (
            not (hostname and ip and os_image) and not non_interactive
        )
    else:
        interactive = not (hostname and ip and iso) and not non_interactive

    # Defaults filled by either interactive or non-interactive branches.
    template_name = ""
    guest_id = ""
    default_user = "root"
    iso_path = iso or ""
    prefix_len = 24

    # =====================================================================
    # INTERACTIVE PATH
    # =====================================================================
    if interactive:
        console.print(
            Panel.fit(
                "[bold]ovbuilder — OpenVox VM Builder[/bold]",
                subtitle="Packer golden clone + interview (or legacy ISO)",
            )
        )

        # --- vCenter credentials (never stored in config by default) ------
        vsphere_server = Prompt.ask(
            "vSphere server FQDN", default=cfg.vsphere_server
        )
        vsphere_user = Prompt.ask("vSphere username")
        vsphere_password = Prompt.ask("vSphere password", password=True)

        console.print(
            f"[dim]Connecting to {vsphere_server} to discover inventory...[/dim]"
        )
        try:
            si = vsphere.connect(
                vsphere_server, vsphere_user, vsphere_password
            )
        except Exception as exc:
            console.print(f"[red]vCenter connection failed: {exc}[/red]")
            raise typer.Exit(1)

        # --- Datacenter / cluster / datastores / networks -----------------
        dcs = vsphere.list_datacenters(si)
        dc = (
            _prompt_table_choice("Datacenters", dcs)
            if dcs
            else Prompt.ask("Datacenter", default=cfg.datacenter)
        )

        clusters = vsphere.list_clusters(si, dc)
        cluster = (
            _prompt_table_choice(f"Clusters in {dc}", clusters)
            if clusters
            else Prompt.ask("Cluster", default=cfg.cluster)
        )

        dss = vsphere.list_datastores(si, dc)
        if not dss:
            vm_ds = Prompt.ask("VM Datastore", default=cfg.vm_datastore)
            iso_ds = Prompt.ask("ISO Datastore", default=cfg.iso_datastore)
        else:
            vm_ds = _prompt_table_choice(f"Datastores in {dc} (VM)", dss)
            if provision_mode == "iso":
                iso_ds = _prompt_table_choice(
                    f"Datastores in {dc} (ISO)", dss
                )
            else:
                # Golden path does not need ISO datastore for clone.
                iso_ds = cfg.iso_datastore

        nets = vsphere.list_networks(si, dc)
        if not nets:
            net_str = Prompt.ask(
                "Networks (comma separated)",
                default=",".join(cfg.networks),
            )
            networks = [n.strip() for n in net_str.split(",") if n.strip()]
        else:
            table = Table(title=f"Networks in {dc}")
            table.add_column("#")
            table.add_column("Name")
            for i, name in enumerate(nets, 1):
                table.add_row(str(i), name)
            console.print(table)
            net_str = Prompt.ask(
                "Select networks (comma separated numbers or names)",
                default="1",
            )
            try:
                networks = []
                for s in [x.strip() for x in net_str.split(",")]:
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

        # --- OS source: golden template vs ISO ----------------------------
        if provision_mode == "golden":
            images = list(cfg.golden_images.items())
            if not images:
                console.print(
                    "[red]No golden_images configured in config.yaml[/red]"
                )
                raise typer.Exit(1)
            table = Table(title="Packer golden images (OS)")
            table.add_column("#", style="cyan")
            table.add_column("Key")
            table.add_column("Template")
            table.add_column("Description")
            for i, (key, gi) in enumerate(images, 1):
                table.add_row(
                    str(i), key, gi.template, gi.description or ""
                )
            console.print(table)
            choice = Prompt.ask("Select OS", default="1")
            try:
                _os_key, gi = images[int(choice) - 1]
            except Exception:
                _os_key, gi = images[0]
            template_name = gi.template
            guest_id = gi.guest_id
            default_user = gi.default_user
            console.print(
                f"[dim]Will clone template [bold]{template_name}[/bold] "
                f"(login user: {default_user})[/dim]"
            )
        else:
            # Legacy ISO: prefer live browser; fall back to known_isos.
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
                iso_path = Prompt.ask(
                    "Enter ISO path (relative to iso datastore, or full)"
                )
            else:
                iso_path = selected

        vsphere.disconnect(si)

        # Persist interview placement onto cfg for the rest of this run only.
        console.print(f"[dim]Using Terraform module at:[/dim] {tf_dir}")
        cfg.vsphere_server = vsphere_server
        cfg.datacenter = dc
        cfg.cluster = cluster
        cfg.vm_datastore = vm_ds
        cfg.iso_datastore = iso_ds
        cfg.networks = networks

        # --- Identity interview -------------------------------------------
        hostname = Prompt.ask("Hostname")
        ip = Prompt.ask("IP Address")
        console.print(
            "[dim]Subnet (CIDR): 0–32, or dotted netmask. Forms: "
            "[bold]19[/bold], [bold]/19[/bold], "
            "[bold]255.255.224.0[/bold][/dim]"
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
        memory = int(
            Prompt.ask("Memory (GB)", default=str(cfg.default_memory_gb))
        )
        disk = int(
            Prompt.ask(
                "Disk (GB, thin provisioned)",
                default=str(cfg.default_disk_gb),
            )
        )

        if not Confirm.ask("Proceed with VM creation?"):
            raise typer.Exit(0)

    # =====================================================================
    # NON-INTERACTIVE PATH
    # =====================================================================
    else:
        if not hostname or not ip:
            console.print(
                "[red]Non-interactive mode requires --hostname and --ip[/red]"
            )
            raise typer.Exit(1)
        if provision_mode == "golden":
            if not os_image:
                console.print(
                    "[red]Golden mode requires --os (e.g. almalinux-10)[/red]"
                )
                raise typer.Exit(1)
            if os_image not in cfg.golden_images:
                console.print(
                    f"[red]Unknown --os {os_image!r}. "
                    f"Configured: {', '.join(cfg.golden_images)}[/red]"
                )
                raise typer.Exit(1)
            gi = cfg.golden_images[os_image]
            template_name = gi.template
            guest_id = gi.guest_id
            default_user = gi.default_user
        else:
            if not iso:
                console.print("[red]ISO mode requires --iso[/red]")
                raise typer.Exit(1)
            iso_path = iso

        try:
            prefix_len = parse_cidr_prefix(
                prefix if prefix is not None else 24
            )
        except PrefixParseError as exc:
            console.print(f"[red]Invalid --prefix/--cidr: {exc}[/red]")
            raise typer.Exit(1)

        cpus = cfg.default_cpus if cpus is None else cpus
        memory = cfg.default_memory_gb if memory is None else memory
        disk = cfg.default_disk_gb if disk is None else disk
        vsphere_server = vsphere_server or cfg.vsphere_server
        cfg.vsphere_server = vsphere_server

    # =====================================================================
    # COMMON: sizing coerce + Terraform apply
    # =====================================================================
    cpus = _coerce_sizing(cpus, cfg.default_cpus)
    memory = _coerce_sizing(memory, cfg.default_memory_gb)
    disk = _coerce_sizing(disk, cfg.default_disk_gb)

    # Guestinfo only for golden clones (ISO guests get network via SSH later).
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

    # Map CLI golden → Terraform clone; keep iso as iso.
    tf_mode = "clone" if provision_mode == "golden" else "iso"

    vm_vars = {
        "vm_name": hostname,
        "iso_path": iso_path or "",
        "provision_mode": tf_mode,
        "template_name": template_name,
        "guest_id": guest_id,
        "guestinfo_extra_config": guestinfo,
        "num_cpus": cpus,
        "memory_mb": memory * 1024,  # interview is GB; Terraform expects MiB
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

    # Defensive re-carry after long interactive block.
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

    # =====================================================================
    # Post-apply: disconnect install media (ISO lock fix)
    # =====================================================================
    def _detach_install_media(reason: str) -> None:
        """
        Disconnect datastore ISO from the VM CD/DVD.

        Required after ISO-mode install; also run as clone hygiene so media
        cannot remain locked if a template still had a CD attached.
        """
        if not (vsphere_server and vsphere_user and vsphere_password):
            console.print(
                "[yellow]Skipping media disconnect "
                "(missing vSphere credentials in this session).[/yellow]\n"
                "[dim]Manually: VM → Edit Settings → CD/DVD → "
                "Client Device / disconnect.[/dim]"
            )
            return
        console.print(f"[cyan]Disconnecting install media ({reason})…[/cyan]")
        try:
            si = vsphere.connect(
                vsphere_server, vsphere_user, vsphere_password
            )
            try:
                result = vsphere.disconnect_install_media(si, hostname)
                if result.get("changed"):
                    console.print(f"[green]✓ {result.get('detail')}[/green]")
                else:
                    console.print(f"[dim]{result.get('detail')}[/dim]")
            finally:
                vsphere.disconnect(si)
        except Exception as exc:
            console.print(
                f"[yellow]Could not disconnect install media automatically: "
                f"{exc}[/yellow]\n"
                "[dim]Disconnect the CD/DVD ISO in vSphere before reboot so "
                "the ISO file is not locked on the datastore.[/dim]"
            )

    # =====================================================================
    # GOLDEN success path
    # =====================================================================
    if provision_mode == "golden":
        _detach_install_media("post-clone hygiene")
        console.print(
            Panel.fit(
                f"[bold green]VM '{hostname}' cloned from {template_name}."
                f"[/bold green]\n\n"
                f"• Identity (cloud-init): [bold]{hostname}[/bold] @ "
                f"[bold]{ip}/{prefix_len}[/bold]\n"
                f"• SSH when Tools/network settle: "
                f"[bold]{default_user}@{ip}[/bold]\n"
                f"• OpenVox agent (after ACLs allow "
                f"{cfg.openvox_server}:8140):\n"
                f"  curl -k --noproxy {cfg.openvox_server} "
                f"https://{cfg.openvox_server}:8140/packages/install.bash "
                f"| sudo bash\n\n"
                "[dim]No console OS install required. "
                "Install media detached.[/dim]",
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
            # configure_network=False: cloud-init already applied identity.
            ok = run_post_install_steps(
                ip=ip,
                username=ssh_user,
                password=ssh_pass,
                hostname=hostname,
                prefix=prefix_len,
                gateway=gateway,
                dns=dns,
                openvox_server=cfg.openvox_server,
                configure_network=False,
            )
            if ok:
                console.print(
                    Panel.fit(
                        "[bold green]Agent bootstrap finished.[/bold green]",
                        title="Done",
                    )
                )
            else:
                console.print(
                    "[red]Bootstrap had errors; check output above.[/red]"
                )
                raise typer.Exit(1)
        return

    # =====================================================================
    # ISO success path
    # =====================================================================
    console.print(
        Panel.fit(
            f"[bold green]VM '{hostname}' created (ISO mode).[/bold green]\n\n"
            "1. Connect to the VM console in vSphere.\n"
            "2. Complete the OS installation.\n"
            "3. Return here so ovbuilder can [bold]disconnect the install "
            "ISO[/bold] (releases the datastore lock and prevents re-booting "
            "the installer).\n"
            f"4. Then set hostname [bold]{hostname}[/bold] / IP "
            f"[bold]{ip}/{prefix_len}[/bold] if not already done.\n"
            "5. Ensure a sudo-capable user exists for optional agent bootstrap.",
            title="Manual OS Installation Required",
        )
    )

    if non_interactive:
        console.print(
            "[yellow]Non-interactive: not waiting for OS install. "
            "Disconnect the CD/DVD ISO in vSphere after install before reboot, "
            "or re-run interactively to auto-detach.[/yellow]"
        )
        return

    if not Confirm.ask(
        "Has the OS install finished? "
        "(ovbuilder will disconnect the install ISO next)"
    ):
        console.print(
            "[yellow]Exiting with ISO still attached. "
            "Disconnect media in vSphere before reboot, or re-run "
            "ovbuilder.[/yellow]"
        )
        return

    # Critical: release datastore ISO lock before reboot into installed OS.
    _detach_install_media("post-install, before reboot into installed OS")
    console.print(
        "[green]Install media disconnected.[/green] "
        "You may reboot the guest into the installed OS now if you have not "
        "already."
    )

    if not Confirm.ask(
        "Can you SSH to the new IP for optional network/agent steps?"
    ):
        console.print("[yellow]Exiting. SSH later when ready.[/yellow]")
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
        configure_network=True,
    )
    if ok:
        console.print(
            Panel.fit(
                "[bold green]Success![/bold green]\n"
                "Node should register with OpenVox.",
                title="Done",
            )
        )
    else:
        console.print("[red]Post-install step encountered errors.[/red]")
        raise typer.Exit(1)
