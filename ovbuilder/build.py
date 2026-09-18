"""
``ovbuilder build`` — interactive / flag-driven VM provisioning.

=============================================================================
END-TO-END FLOWS
=============================================================================

Golden (default)
  1. Discover vSphere inventory (optional interactive menus).
  2. Select an ``ovbuilder-*`` golden from live vCenter (name + short label).
     Template datacenter is resolved silently (cross-DC clone is OK).
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
from .goldens import (
    COMPUTE_TYPE_CLUSTER,
    COMPUTE_TYPE_HOST,
    discover_goldens,
    goldens_from_config,
    resolve_os_image,
)
from .openvox_site import DEFAULT_SITES, infer_location, site_for
from .config import OvbuilderConfig, get_config_manager
from .network import (
    PrefixParseError,
    is_plausible_dns,
    normalize_dns_servers,
    parse_cidr_prefix,
    prefix_to_netmask,
)
from .secrets import (
    allow_password_ssh,
    get_golden_password,
    get_vsphere_password,
    golden_password_setup_help,
    secrets_env_path,
)
from .ssh import run_post_install_steps
from .terraform import run_terraform_apply

console = Console()


def _ensure_golden_password_configured() -> None:
    """
    Fail early when password SSH is opted in but no local password is set.

    Prints a clear panel with the resolved secrets path and platform-specific
    setup steps so the operator is not surprised mid-interview.
    """
    if get_golden_password():
        return
    path = secrets_env_path()
    console.print(
        Panel.fit(
            "[bold red]Golden password not configured[/bold red]\n\n"
            "Password SSH is opted in ([bold]OVBUILDER_ALLOW_PASSWORD_SSH[/bold]), so "
            "clones need a guest login password from a [bold]local[/bold] secrets "
            "file (never committed to git).\n\n"
            f"Expected file: [cyan]{path}[/cyan]\n\n"
            "See the printed commands below (UNIX and Windows), then re-run:\n"
            "  [bold]ovbuilder build[/bold]\n\n"
            "[dim]Full guide: docs/SECRETS.md[/dim]",
            title="Missing secrets.env",
            border_style="red",
        )
    )
    # Full multi-line help also goes to stderr-style console for copy/paste.
    console.print(f"\n[dim]{golden_password_setup_help()}[/dim]\n")
    raise typer.Exit(1)


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


def apply_cli_placement(
    cfg: OvbuilderConfig,
    *,
    cluster: Optional[str] = None,
    networks: Optional[List[str]] = None,
    vm_datastore_cluster: Optional[str] = None,
    datacenter: Optional[str] = None,
) -> None:
    """Apply non-interactive placement flags onto the in-memory config."""
    if cluster:
        cfg.cluster = cluster.strip()
    if networks:
        parsed: List[str] = []
        for item in networks:
            if not item:
                continue
            parsed.extend(part.strip() for part in str(item).split(",") if part.strip())
        if parsed:
            cfg.networks = parsed
    if vm_datastore_cluster:
        cfg.vm_datastore_cluster = vm_datastore_cluster.strip()
    if datacenter:
        cfg.datacenter = datacenter.strip()


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


def _compute_picker_rows(
    clusters: List[str], hosts: List[str]
) -> List[Tuple[str, str]]:
    """(name, kind) rows: DRS clusters first, then standalone ESXi hosts."""
    rows: List[Tuple[str, str]] = [
        (name, COMPUTE_TYPE_CLUSTER) for name in clusters
    ]
    rows.extend((name, COMPUTE_TYPE_HOST) for name in hosts)
    return rows


def _prompt_compute_placement(si, dc: str) -> Tuple[str, str]:
    """
    Pick compute and always classify via ``classify_compute``.

    The table labels each row as a DRS cluster or standalone ESXi host so
    SEA3 FQDNs are not mistaken for ClusterComputeResource names.
    """
    clusters = vsphere.list_clusters(si, dc)
    hosts = vsphere.list_standalone_hosts(si, dc)
    rows = _compute_picker_rows(clusters, hosts)
    if not rows:
        console.print(
            f"[red]No vSphere compute cluster (ClusterComputeResource) and no "
            f"standalone ESXi host found in datacenter {dc!r}.[/red]\n"
            "[dim]ovbuilder requires a DRS/HA cluster, or a standalone host "
            "when the datacenter has no cluster.[/dim]"
        )
        raise typer.Exit(1)
    table = Table(title=f"Compute in {dc}")
    table.add_column("#")
    table.add_column("Name")
    table.add_column("Type")
    for i, (name, kind) in enumerate(rows, 1):
        label = (
            "cluster (DRS/HA)"
            if kind == COMPUTE_TYPE_CLUSTER
            else "standalone ESXi host"
        )
        table.add_row(str(i), name, label)
    console.print(table)
    choice = Prompt.ask("Select", default="1")
    try:
        selected = rows[int(choice) - 1][0]
    except Exception:
        selected = rows[0][0]
    try:
        compute_type = vsphere.classify_compute(si, dc, selected)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    return selected, compute_type


def _verify_clone_template(
    si,
    *,
    template_name: str,
    template_datacenter: str,
    placement_datacenter: str,
) -> str:
    """Fail before Terraform if the Packer template is missing in every DC."""
    preferred = (template_datacenter or placement_datacenter or "").strip()
    try:
        return vsphere.require_template(si, template_name, preferred)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _resolve_golden_and_compute(
    cfg: OvbuilderConfig,
    *,
    vsphere_server: Optional[str],
    vsphere_user: Optional[str],
    vsphere_password: Optional[str],
    os_image: Optional[str],
    template_name: str,
    guest_id: str,
    default_user: str,
    template_datacenter: str,
    compute_type: str,
    cluster_name: Optional[str],
    datacenter: Optional[str],
) -> Tuple[str, str, str, str, str]:
    """
    Silently resolve golden source DC and cluster-vs-host placement.

    The operator never sees template datacenter or datastore. Live inventory
    is preferred; config goldens are an offline fallback.
    """
    needle = (os_image or template_name or "").strip()

    def _from_config_only() -> Tuple[str, str, str, str, str]:
        if not needle:
            return template_name, guest_id, default_user, template_datacenter, compute_type
        try:
            chosen = resolve_os_image(needle, [], cfg)
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        return (
            chosen.name,
            guest_id or chosen.guest_id,
            chosen.default_user or default_user,
            template_datacenter,
            compute_type,
        )

    if not (vsphere_server and vsphere_user and vsphere_password):
        return _from_config_only()

    si = None
    try:
        si = vsphere.connect(vsphere_server, vsphere_user, vsphere_password)
        live = []
        try:
            live = discover_goldens(si, cfg)
        except Exception as exc:
            console.print(
                f"[yellow]Could not list ovbuilder-* templates: {exc}[/yellow]"
            )
        if needle:
            try:
                chosen = resolve_os_image(needle, live, cfg)
            except KeyError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1) from exc
            template_name = chosen.name
            guest_id = guest_id or chosen.guest_id
            default_user = chosen.default_user or default_user
            if chosen.datacenter:
                template_datacenter = chosen.datacenter
        if cluster_name:
            try:
                compute_type = vsphere.classify_compute(
                    si, datacenter or cfg.datacenter, cluster_name
                )
            except RuntimeError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1) from exc
        if template_name:
            template_datacenter = _verify_clone_template(
                si,
                template_name=template_name,
                template_datacenter=template_datacenter,
                placement_datacenter=datacenter or cfg.datacenter,
            )
        return (
            template_name,
            guest_id,
            default_user,
            template_datacenter,
            compute_type,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        if vsphere.looks_like_host_fqdn(cluster_name or ""):
            console.print(
                f"[red]Could not classify compute {cluster_name!r} ({exc}). "
                "That name looks like an ESXi host FQDN; refusing to treat it "
                "as a DRS cluster (vsphere_compute_cluster would fail). "
                "Reinstall ovbuilder from current staging if /opt/ovbuilder "
                "is older than standalone-host placement.[/red]"
            )
            raise typer.Exit(1) from exc
        console.print(
            f"[yellow]Could not resolve golden/host placement from vCenter: "
            f"{exc}[/yellow]"
        )
        return _from_config_only()
    finally:
        if si is not None:
            vsphere.disconnect(si)


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
        help="Golden image key or ovbuilder-* template name (e.g. ubuntu-24.04)",
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
    dns: Optional[List[str]] = typer.Option(
        None,
        "--dns",
        help=(
            "DNS server IP or hostname. Repeat the flag or comma-separate. "
            "Interactive: enter one per prompt; empty line finishes."
        ),
    ),
    skip_dnf_groups: bool = typer.Option(
        False,
        "--skip-dnf-groups",
        help="Do not install configured DNF groups at clone/post-install time",
    ),
    location: Optional[str] = typer.Option(
        None,
        "--location",
        help="Site code (ATLC, PDXC, …). Compiler VIP is chosen from this.",
    ),
    cluster: Optional[str] = typer.Option(
        None, "--cluster", help="Compute cluster or standalone ESXi host name"
    ),
    network: Optional[List[str]] = typer.Option(
        None,
        "--network",
        help="Port group / network name. Repeat the flag or comma-separate.",
    ),
    vm_datastore_cluster: Optional[str] = typer.Option(
        None,
        "--vm-datastore-cluster",
        help="Storage DRS cluster (e.g. YAVIN-DEV). Preferred over a single LUN.",
    ),
    datacenter: Optional[str] = typer.Option(
        None, "--datacenter", help="vSphere datacenter name"
    ),
):
    """
    Build a VM from a Packer golden template (default) or legacy ISO.

    Interactive: select OS from live ovbuilder-* templates → interview
    (host/IP/CIDR/…) → Terraform apply. Template source DC is never prompted.
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
    if _is_optioninfo(skip_dnf_groups):
        skip_dnf_groups = False
    if _is_optioninfo(location):
        location = None
    if _is_optioninfo(cluster):
        cluster = None
    if _is_optioninfo(network):
        network = None
    if _is_optioninfo(vm_datastore_cluster):
        vm_datastore_cluster = None
    if _is_optioninfo(datacenter):
        datacenter = None

    # Prefer env / secrets.env over ``--vsphere-password`` on argv (ps-visible).
    vsphere_password = get_vsphere_password(vsphere_password)

    # --- Config + terraform path --------------------------------------------
    cfg: OvbuilderConfig = (
        ctx.obj.get("config") if ctx.obj else get_config_manager().load_config()
    )
    tf_dir = get_config_manager().get_effective_terraform_dir()
    provision_mode = _normalize_provision_mode(mode, cfg.provision_mode)

    # Password SSH is opt-in. Only then require a local golden password
    # before interview / vCenter work so the operator sees the fix first.
    if provision_mode == "golden" and allow_password_ssh():
        _ensure_golden_password_configured()

    # Interactive when required identity pieces are missing (unless -y).
    if provision_mode == "golden":
        interactive = (
            not (hostname and ip and os_image) and not non_interactive
        )
    else:
        interactive = not (hostname and ip and iso) and not non_interactive

    # Defaults filled by either interactive or non-interactive branches.
    template_name = ""
    template_datacenter = ""
    compute_type = COMPUTE_TYPE_CLUSTER
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
        if not vsphere_password:
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

        cluster, compute_type = _prompt_compute_placement(si, dc)

        dss = vsphere.list_datastores(si, dc)
        dscs = vsphere.list_datastore_clusters(si, dc)
        vm_dsc = ""
        if dscs:
            # Prefer Storage DRS clusters (YAVIN-*, HOTH_*). Last row
            # falls back to a single datastore when SDRS is not wanted.
            cluster_choices = list(dscs) + ["Single datastore (pick a LUN)"]
            picked = _prompt_table_choice(
                f"Datastore clusters in {dc} (VM disks)",
                cluster_choices,
            )
            if picked == "Single datastore (pick a LUN)":
                vm_ds = (
                    _prompt_table_choice(f"Datastores in {dc} (VM)", dss)
                    if dss
                    else Prompt.ask("VM Datastore", default=cfg.vm_datastore)
                )
            else:
                vm_dsc = picked
                vm_ds = cfg.vm_datastore
        elif dss:
            vm_ds = _prompt_table_choice(f"Datastores in {dc} (VM)", dss)
        else:
            vm_ds = Prompt.ask("VM Datastore", default=cfg.vm_datastore)

        if provision_mode == "iso":
            iso_ds = (
                _prompt_table_choice(f"Datastores in {dc} (ISO library)", dss)
                if dss
                else Prompt.ask("ISO Datastore", default=cfg.iso_datastore)
            )
        else:
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
            live = []
            try:
                live = discover_goldens(si, cfg)
            except Exception as exc:
                console.print(
                    f"[yellow]Could not list ovbuilder-* templates: {exc}[/yellow]"
                )
            goldens = live or goldens_from_config(cfg)
            if not goldens:
                console.print(
                    "[red]No ovbuilder-* templates found in vCenter "
                    "and none configured in config.yaml[/red]"
                )
                raise typer.Exit(1)
            if not live:
                console.print(
                    "[yellow]Using configured ovbuilder-* goldens "
                    "(live inventory empty or unavailable).[/yellow]"
                )
            table = Table(title="OS templates")
            table.add_column("#", style="cyan")
            table.add_column("Name")
            table.add_column("OS")
            for i, gi_row in enumerate(goldens, 1):
                table.add_row(str(i), gi_row.name, gi_row.label)
            console.print(table)
            choice = Prompt.ask("Select OS", default="1")
            try:
                chosen = goldens[int(choice) - 1]
            except Exception:
                chosen = goldens[0]
            template_name = chosen.name
            guest_id = chosen.guest_id
            default_user = chosen.default_user
            template_datacenter = chosen.datacenter
            os_image = chosen.key
            template_datacenter = _verify_clone_template(
                si,
                template_name=template_name,
                template_datacenter=template_datacenter,
                placement_datacenter=dc,
            )
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
        cfg.vm_datastore_cluster = vm_dsc
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

        console.print(
            "[dim]DNS servers (optional). Enter one IP or hostname per prompt. "
            "Leave blank and press Enter when done.[/dim]"
        )
        dns_acc: List[str] = []
        while True:
            idx = len(dns_acc) + 1
            raw_dns = Prompt.ask(
                f"DNS server #{idx} (empty to finish)",
                default="",
            )
            value = (raw_dns or "").strip()
            if not value:
                break
            if not is_plausible_dns(value):
                console.print(
                    f"[red]Invalid DNS server {value!r}. "
                    "Use an IPv4/IPv6 address or hostname.[/red]"
                )
                continue
            if value in dns_acc:
                console.print(f"[yellow]Already added {value}; skipping.[/yellow]")
                continue
            dns_acc.append(value)
            console.print(f"[dim]  + {value}[/dim]")
        dns = dns_acc
        if dns:
            console.print(f"[dim]DNS order: {', '.join(dns)}[/dim]")
        else:
            console.print("[dim]No DNS servers entered.[/dim]")

        guessed = infer_location(
            location=location,
            hostname=hostname or "",
            domain=cfg.domain,
            networks=cfg.networks,
        )
        loc_default = guessed or "PDXC"
        location = Prompt.ask(
            f"Location ({', '.join(sorted(DEFAULT_SITES))})",
            default=loc_default,
        ).strip().upper()

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
                    "[red]Golden mode requires --os (e.g. ubuntu-24.04 "
                    "or ovbuilder-ubuntu-24.04)[/red]"
                )
                raise typer.Exit(1)
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
        apply_cli_placement(
            cfg,
            cluster=cluster,
            networks=network,
            vm_datastore_cluster=vm_datastore_cluster,
            datacenter=datacenter,
        )
        if provision_mode == "golden":
            (
                template_name,
                guest_id,
                default_user,
                template_datacenter,
                compute_type,
            ) = _resolve_golden_and_compute(
                cfg,
                vsphere_server=vsphere_server,
                vsphere_user=vsphere_user,
                vsphere_password=vsphere_password,
                os_image=os_image,
                template_name=template_name,
                guest_id=guest_id,
                default_user=default_user,
                template_datacenter=template_datacenter,
                compute_type=compute_type,
                cluster_name=cfg.cluster,
                datacenter=cfg.datacenter,
            )

    # =====================================================================
    # COMMON: sizing coerce + Terraform apply
    # =====================================================================
    cpus = _coerce_sizing(cpus, cfg.default_cpus)
    memory = _coerce_sizing(memory, cfg.default_memory_gb)
    disk = _coerce_sizing(disk, cfg.default_disk_gb)

    # Normalize DNS from interactive list or --dns flags / comma lists.
    dns = normalize_dns_servers(dns)
    bad_dns = [d for d in dns if not is_plausible_dns(d)]
    if bad_dns:
        console.print(
            f"[red]Invalid --dns value(s): {', '.join(bad_dns)}[/red]"
        )
        raise typer.Exit(1)
    if dns:
        console.print(f"[dim]DNS servers: {', '.join(dns)}[/dim]")

    # Clone-time DNF groups (EL only; skipped on Ubuntu / when flag set).
    dnf_groups = [] if skip_dnf_groups else list(cfg.dnf_groups or [])
    if dnf_groups:
        console.print(
            f"[dim]DNF groups at provision time: {', '.join(dnf_groups)}[/dim]"
        )

    if not location:
        location = infer_location(
            hostname=hostname or "",
            domain=cfg.domain,
            networks=cfg.networks,
        )
    location = (location or "").strip().upper()
    try:
        ov_site = site_for(location) if location else None
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if ov_site:
        console.print(
            f"[dim]OpenVox site {location}: server={ov_site.compiler} "
            f"ca_server={ov_site.ca_server} gui={ov_site.gui}[/dim]"
        )
    else:
        console.print(
            "[yellow]No OpenVox location resolved; "
            "clone will skip agent install. Pass --location.[/yellow]"
        )

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
            default_user=default_user,
            dnf_groups=dnf_groups,
            openvox_site=ov_site,
            allow_password_ssh=allow_password_ssh(),
        )

    # Map CLI golden → Terraform clone; keep iso as iso.
    tf_mode = "clone" if provision_mode == "golden" else "iso"

    vm_vars = {
        "vm_name": hostname,
        "iso_path": iso_path or "",
        "provision_mode": tf_mode,
        "template_name": template_name,
        "template_datacenter": template_datacenter,
        "compute_type": compute_type,
        "guest_id": guest_id,
        "guestinfo_extra_config": guestinfo,
        "num_cpus": cpus,
        "memory_mb": memory * 1024,  # interview is GB; Terraform expects MiB
        "disk_size_gb": disk,
        "networks": cfg.networks,
        "datacenter": cfg.datacenter,
        "cluster": cfg.cluster,
        "vm_datastore": cfg.vm_datastore,
        "vm_datastore_cluster": getattr(cfg, "vm_datastore_cluster", "") or "",
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
                f"[bold]{default_user}@{ip}[/bold] "
                "(key-based by default; password SSH is opt-in)\n"
                f"• OpenVox: location [bold]{location or 'unset'}[/bold] "
                f"server=[bold]{ov_site.compiler if ov_site else cfg.openvox_server}[/bold] "
                f"ca=[bold]{ov_site.ca_server if ov_site else 'ovca.corp.int-x.ai'}[/bold]\n\n"
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
            if not allow_password_ssh():
                console.print(
                    "[yellow]Clone-time SSH password auth is off by default. "
                    "This prompt only works if the guest already has your key "
                    "or you set OVBUILDER_ALLOW_PASSWORD_SSH=1 and rebuilt.[/yellow]"
                )
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
                openvox_site=ov_site,
                configure_network=False,
                # Golden cloud-init already ran groups; do not re-run over SSH.
                dnf_groups=[],
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
        openvox_site=ov_site,
        configure_network=True,
        dnf_groups=dnf_groups,
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
