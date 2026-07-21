"""
Post-provision SSH steps: optional network touch-up + OpenVox agent bootstrap.

=============================================================================
WHEN THIS RUNS
=============================================================================
* **Golden path:** cloud-init already set hostname/IP. This module is only
  used if the operator opts into agent install. Network reconfiguration is
  skipped when ``configure_network=False`` (default for golden).
* **ISO path:** after OS install + media disconnect, optional SSH can set
  network (best-effort) and always can run the agent bootstrap script.

=============================================================================
SECURITY
=============================================================================
* Passwords are not logged.
* Hostname/IP are lightly sanitized before embedding in remote shell strings
  (reject shell metacharacters) to reduce injection risk from interview typos.
* Agent bootstrap still uses ``curl | sudo bash`` — that matches the official
  OpenVox/Puppet agent install pattern; ACLs to :8140 must already allow it.
"""

from __future__ import annotations

import re
from typing import Optional

import paramiko
from rich.console import Console
from rich.panel import Panel

console = Console()

# Allow only safe tokens in shell-interpolated identity fields.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:/-]+$")


def _require_safe(label: str, value: str) -> str:
    """
    Reject values that could break out of remote shell quoting.

    We still pass values unquoted into some remote commands for compatibility
    with simple guest shells; this is a hard gate on metacharacters.
    """
    if not value or not _SAFE_TOKEN.match(value):
        raise ValueError(f"Refusing unsafe {label} for remote shell: {value!r}")
    return value


def _configure_network(
    client: paramiko.SSHClient,
    hostname: str,
    ip: str,
    prefix: int = 24,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
) -> None:
    """
    Best-effort static network configuration inside the guest.

    Tries, in order:
      1. hostnamectl
      2. nmcli (RHEL/Alma — connection name 'System eth0' is a common default)
      3. netplan (Ubuntu)
      4. raw ``ip addr add`` (last resort, non-persistent)

    Gateway/DNS are only applied when provided — we do **not** invent
    10.0.0.1 defaults (that previously forced wrong routes in foreign subnets).
    """
    console.print("[yellow]Configuring hostname and network inside guest...[/yellow]")

    hostname = _require_safe("hostname", hostname)
    ip = _require_safe("ip", ip)
    if not isinstance(prefix, int) or not 0 <= prefix <= 32:
        raise ValueError(f"invalid prefix: {prefix!r}")

    commands = [
        f"hostnamectl set-hostname {hostname} || true",
    ]

    # Optional gateway/DNS — omit nmcli keys when unset.
    gw = (gateway or "").strip()
    dns1 = (dns or "").strip()
    if gw:
        gw = _require_safe("gateway", gw)
    if dns1:
        dns1 = _require_safe("dns", dns1)

    # --- RHEL-family (NetworkManager) ---------------------------------------
    nmcli = (
        f"nmcli con mod 'System eth0' "
        f"ipv4.addresses {ip}/{prefix} "
        f"ipv4.method manual"
    )
    if gw:
        nmcli += f" ipv4.gateway {gw}"
    if dns1:
        nmcli += f" ipv4.dns {dns1}"
    nmcli += " || true"
    commands.append(nmcli)
    commands.append("nmcli con up 'System eth0' || true")

    # --- Ubuntu (netplan) ---------------------------------------------------
    # Only write addresses; routes/nameservers only if supplied.
    routes_yaml = ""
    if gw:
        routes_yaml = (
            f"      routes:\n"
            f"        - to: default\n"
            f"          via: {gw}\n"
        )
    dns_yaml = ""
    if dns1:
        dns_yaml = (
            f"      nameservers:\n"
            f"        addresses: [{dns1}]\n"
        )
    netplan = (
        "network:\n"
        "  version: 2\n"
        "  ethernets:\n"
        "    eth0:\n"
        "      dhcp4: false\n"
        f"      addresses: [{ip}/{prefix}]\n"
        f"{routes_yaml}"
        f"{dns_yaml}"
    )
    # Quoted heredoc so guest shell does not expand anything.
    commands.append(f"cat > /etc/netplan/01-netcfg.yaml << 'EON'\n{netplan}EON")
    commands.append("netplan apply || true")

    # --- Last resort (non-persistent) ---------------------------------------
    commands.append(f"ip addr add {ip}/{prefix} dev eth0 2>/dev/null || true")

    try:
        # Join with && so we stop early only if a hard failure occurs;
        # individual steps already end with || true.
        client.exec_command(" && ".join(commands), timeout=90)
    except Exception as e:
        console.print(f"[dim]Network config warning (non-fatal): {e}[/dim]")


def run_post_install_steps(
    ip: str,
    username: str,
    password: str,
    hostname: str,
    prefix: int = 24,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    openvox_server: str = "openvox.example.com",
    configure_network: bool = True,
) -> bool:
    """
    SSH to the guest and optionally configure network + run agent bootstrap.

    Parameters
    ----------
    configure_network : bool
        False for golden clones (cloud-init already applied identity).
        True for ISO path when the guest still needs static config.

    Returns
    -------
    bool
        True if agent bootstrap exit code was 0.
    """
    console.print(Panel(f"Connecting to {ip} as {username} ...", title="Post-Install"))

    try:
        client = paramiko.SSHClient()
        # First boot of a lab VM: accept unknown host keys (lab trade-off).
        # Production hardening could pin keys or use known_hosts.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, username=username, password=password, timeout=15)
    except Exception as e:
        console.print(f"[red]SSH connection failed: {e}[/red]")
        return False

    try:
        if configure_network:
            try:
                _configure_network(client, hostname, ip, prefix, gateway, dns)
            except ValueError as exc:
                console.print(f"[yellow]Skipping network config: {exc}[/yellow]")

        # Official agent install entrypoint on OpenVox server :8140/packages
        openvox_server = _require_safe("openvox_server", openvox_server)
        cmd = (
            f"curl -k --noproxy {openvox_server} "
            f"https://{openvox_server}:8140/packages/install.bash | sudo bash"
        )

        console.print(Panel(f"[bold]Running:[/bold] {cmd}", title="OpenVox Bootstrap"))

        # get_pty=True so sudo/agent installers that need a TTY still work.
        _stdin, stdout, _stderr = client.exec_command(cmd, get_pty=True, timeout=300)
        for line in iter(stdout.readline, ""):
            console.print(line.rstrip())
        exit_code = stdout.channel.recv_exit_status()

        if exit_code == 0:
            console.print("[green]✓ OpenVox agent bootstrap completed[/green]")
            return True
        console.print(f"[red]Bootstrap script exited with status {exit_code}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Error during bootstrap: {e}[/red]")
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass
