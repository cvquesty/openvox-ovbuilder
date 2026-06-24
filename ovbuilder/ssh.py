"""
Post-provision SSH steps for ovbuilder.

After the OS is installed:
- Configure hostname and static IP (best effort)
- Run the OpenVox agent bootstrap script
"""

import time
from typing import Optional

import paramiko
from rich.console import Console
from rich.panel import Panel

console = Console()


def _configure_network(
    client: paramiko.SSHClient,
    hostname: str,
    ip: str,
    prefix: int = 24,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
):
    console.print("[yellow]Configuring hostname and network inside guest...[/yellow]")

    commands = [
        f"hostnamectl set-hostname {hostname} || true",
    ]

    # RHEL-family (nmcli)
    gw = gateway or "10.0.0.1"
    dns1 = dns or "10.0.0.10"
    commands.append(
        f"nmcli con mod 'System eth0' "
        f"ipv4.addresses {ip}/{prefix} "
        f"ipv4.gateway {gw} "
        f"ipv4.dns {dns1} "
        f"ipv4.method manual || true"
    )
    commands.append("nmcli con up 'System eth0' || true")

    # Ubuntu / netplan
    netplan = f"""network:
  version: 2
  ethernets:
    eth0:
      dhcp4: false
      addresses: [{ip}/{prefix}]
      routes:
        - to: default
          via: {gw}
      nameservers:
        addresses: [{dns1}]
"""
    commands.append(f"cat > /etc/netplan/01-netcfg.yaml << 'EON'\n{netplan}EON")
    commands.append("netplan apply || true")

    # Last resort
    commands.append(f"ip addr add {ip}/{prefix} dev eth0 2>/dev/null || true")

    try:
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
) -> bool:
    """Connect via SSH and perform post-OS-install steps."""

    console.print(Panel(f"Connecting to {ip} as {username} ...", title="Post-Install"))

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, username=username, password=password, timeout=15)
    except Exception as e:
        console.print(f"[red]SSH connection failed: {e}[/red]")
        return False

    # Configure network/hostname (best effort)
    _configure_network(client, hostname, ip, prefix, gateway, dns)

    # Run the OpenVox installer
    cmd = (
        f"curl -k --noproxy {openvox_server} "
        f"https://{openvox_server}:8140/packages/install.bash | sudo bash"
    )

    console.print(Panel(f"[bold]Running:[/bold] {cmd}", title="OpenVox Bootstrap"))

    try:
        stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=300)
        for line in iter(stdout.readline, ""):
            console.print(line.rstrip())
        exit_code = stdout.channel.recv_exit_status()
        client.close()

        if exit_code == 0:
            console.print("[green]✓ OpenVox agent bootstrap completed[/green]")
            return True
        else:
            console.print(f"[red]Bootstrap script exited with status {exit_code}[/red]")
            return False
    except Exception as e:
        console.print(f"[red]Error during bootstrap: {e}[/red]")
        try:
            client.close()
        except Exception:
            pass
        return False
