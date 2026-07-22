"""
cloud-init guestinfo payloads for Packer golden-image clones.

=============================================================================
WHY
=============================================================================
After Terraform clones a golden template, the guest must learn its
per-instance identity without a console install:

  * hostname / FQDN
  * static IPv4 on the **existing** primary NIC (ens33 / ens192 / eth0 / …)

VMware Tools + cloud-init read ``guestinfo.metadata`` and
``guestinfo.userdata`` from the VM's extraConfig (set by Terraform).

=============================================================================
NETWORK STRATEGY (simplified — do not invent new NM profiles)
=============================================================================
cloud-init's Network Config v2 renderer on Alma/RHEL creates *new*
NetworkManager profiles such as ``cloud-init nics`` while leaving the
stock ``Wired connection 1`` on ens33. Result: nmtui shows a pretty
profile, ``ip addr`` has no address, boot looks broken.

We therefore:

  1. Tell cloud-init **not** to manage network at all
     (``network: {config: disabled}`` in user-data).
  2. In ``runcmd``, use **nmcli** to find the existing primary ethernet
     connection (or the one already bound to the first ``e*`` iface) and
     **modify it in place** — same interface, same connection name when
     possible — then ``nmcli connection up``.
  3. Delete leftover ethernet profiles that would fight for the NIC
     (other "Wired connection *" / "cloud-init *" clones).

Hostname still comes from metadata + user-data as usual.
"""

from __future__ import annotations

import base64
import shlex
from typing import Optional

# Baked into Packer goldens (Alma kickstart + Ubuntu autoinstall).
# Also re-applied at clone time so a broken golden password does not brick login.
GOLDEN_DEFAULT_PASSWORD = "ChangeMe-BuildOnly!"


def _b64(s: str) -> str:
    """UTF-8 → base64 ASCII for guestinfo.* values."""
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def build_metadata(hostname: str, domain: str = "") -> str:
    """
    Build cloud-init **metadata** YAML (instance-id + hostname only).

    No network block — network is applied via nmcli in user-data runcmd.
    """
    _ = domain
    return (
        f"instance-id: {hostname}\n"
        f"local-hostname: {hostname}\n"
        f"hostname: {hostname}\n"
    )


def _nmcli_configure_script(
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    domain: str = "",
) -> str:
    """
    Bash script body (no shebang) that reconfigures the primary ethernet
    connection in place via nmcli.
    """
    gw = (gateway or "").strip()
    dns1 = (dns or "").strip()
    dom = (domain or "").strip()

    # Build optional nmcli modify fragments (already shell-safe values).
    extra_mod: list[str] = []
    if gw:
        extra_mod.append(f"ipv4.gateway {shlex.quote(gw)}")
    else:
        extra_mod.append("ipv4.gateway ''")
    if dns1:
        extra_mod.append(f"ipv4.dns {shlex.quote(dns1)}")
    else:
        extra_mod.append("ipv4.dns ''")
    if dom:
        extra_mod.append(f"ipv4.dns-search {shlex.quote(dom)}")
    else:
        extra_mod.append("ipv4.dns-search ''")
    extra = " ".join(extra_mod)

    # Script uses only single-quoted nmcli args via shlex for IP pieces.
    ip_cidr = shlex.quote(f"{ip}/{prefix}")

    return f"""
set +e
# Primary ethernet: first e* device (ens33, ens192, eth0, …)
IFACE=$(ls -1 /sys/class/net 2>/dev/null | grep -E '^e' | head -1)
if [ -z "$IFACE" ]; then
  echo "ovbuilder-net: no ethernet interface found" >&2
  exit 1
fi
echo "ovbuilder-net: primary iface=$IFACE"

# Prefer connection already bound to that device (any name)
CONN=$(nmcli -t -f NAME,DEVICE connection show 2>/dev/null \\
  | awk -F: -v i="$IFACE" '$2 == i {{ print $1; exit }}')

# Else first ethernet-type connection
if [ -z "$CONN" ]; then
  CONN=$(nmcli -t -f NAME,TYPE connection show 2>/dev/null \\
    | awk -F: '$2 == "802-3-ethernet" {{ print $1; exit }}')
fi

# Create only if none exists — always named exactly after the iface
if [ -z "$CONN" ]; then
  nmcli connection add type ethernet ifname "$IFACE" con-name "$IFACE" \\
    ipv4.method manual ipv6.method ignore
  CONN="$IFACE"
  echo "ovbuilder-net: created connection $CONN"
else
  echo "ovbuilder-net: found connection '$CONN' — will rename to $IFACE"
fi

# Configure in place AND force profile name to the iface only
# (must not stay as "cloud-init ens33" or "Wired connection 1").
# connection.id is the NM profile name shown in nmtui/nmcli.
nmcli connection modify "$CONN" \\
  connection.id "$IFACE" \\
  connection.interface-name "$IFACE" \\
  connection.autoconnect yes \\
  connection.autoconnect-priority 100 \\
  ipv4.method manual \\
  ipv4.addresses {ip_cidr} \\
  ipv6.method ignore \\
  {extra}

# After rename, the profile is always $IFACE
CONN="$IFACE"

# Drop every other ethernet profile (cloud-init *, Wired connection *, …)
nmcli -t -f NAME,TYPE connection show 2>/dev/null | while IFS=: read -r name typ; do
  [ "$typ" = "802-3-ethernet" ] || continue
  [ "$name" = "$CONN" ] && continue
  echo "ovbuilder-net: deleting spare profile '$name'"
  nmcli connection delete "$name" 2>/dev/null || true
done

# Activate under the clean name
nmcli connection up "$CONN" || nmcli device reapply "$IFACE" || true
echo "ovbuilder-net: done (connection name=$CONN)"
nmcli -t -f NAME,DEVICE,FILENAME connection show --active 2>/dev/null | head -20
ip -br addr show "$IFACE" || true
""".strip()


def build_userdata(
    hostname: str,
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    domain: str = "",
    default_user: str = "ubuntu",
    password: str = GOLDEN_DEFAULT_PASSWORD,
) -> str:
    """
    Build cloud-init **user-data**: hostname, passwords, disable CI network, nmcli.
    """
    fqdn = f"{hostname}.{domain}" if domain else hostname
    script = _nmcli_configure_script(
        ip, prefix, gateway=gateway, dns=dns, domain=domain
    )
    # Embed script as write_files content (YAML | block needs > key indent).
    body_lines = ["#!/bin/bash", *script.splitlines()]
    script_block = "\n".join(
        f"      {line}" if line else "      " for line in body_lines
    )
    # default_user is almalinux or ubuntu depending on golden; also set root.
    user = (default_user or "ubuntu").strip() or "ubuntu"
    pw = password or GOLDEN_DEFAULT_PASSWORD
    # chpasswd list: user:pass one per line (cloud-init classic form)
    chpasswd_list = f"{user}:{pw}\\nroot:{pw}"

    lines = [
        "#cloud-config",
        f"hostname: {hostname}",
        f"fqdn: {fqdn}",
        "prefer_fqdn_over_hostname: true",
        "manage_etc_hosts: true",
        "package_update: false",
        "package_upgrade: false",
        "ssh_pwauth: true",
        "disable_root: false",
        # Re-assert golden password on first clone boot (fixes broken goldens).
        "chpasswd:",
        "  expire: false",
        "  list: |",
        f"    {user}:{pw}",
        f"    root:{pw}",
        "# Do NOT let cloud-init invent NM profiles (orphan nics/eth0 names).",
        "network:",
        "  config: disabled",
        "bootcmd:",
        "  - [systemctl, disable, --now, NetworkManager-wait-online.service]",
        "write_files:",
        "  - path: /usr/local/sbin/ovbuilder-net.sh",
        "    permissions: '0755'",
        "    owner: root:root",
        "    content: |",
        script_block,
        "runcmd:",
        f"  - [hostnamectl, set-hostname, {hostname}]",
        # Belt-and-suspenders if chpasswd module order races users
        f"  - [bash, -c, \"echo '{user}:{pw}' | chpasswd; echo 'root:{pw}' | chpasswd\"]",
        "  - [/usr/local/sbin/ovbuilder-net.sh]",
        f'final_message: "ovbuilder cloud-init finished for {hostname}"',
        "",
    ]
    _ = chpasswd_list  # reserved if we switch formats
    return "\n".join(lines)


def guestinfo_extra_config(
    hostname: str,
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    domain: str = "",
    default_user: str = "ubuntu",
    password: str = GOLDEN_DEFAULT_PASSWORD,
) -> dict:
    """
    Return a dict for Terraform ``extra_config`` / ``guestinfo_extra_config``.

    Keys
    ----
    guestinfo.metadata / .encoding — instance-id + hostname only
    guestinfo.userdata / .encoding — hostname, passwords, network, nmcli
    """
    meta = build_metadata(hostname, domain=domain)
    user = build_userdata(
        hostname,
        ip,
        prefix,
        gateway=gateway,
        dns=dns,
        domain=domain,
        default_user=default_user,
        password=password,
    )
    return {
        "guestinfo.metadata": _b64(meta),
        "guestinfo.metadata.encoding": "base64",
        "guestinfo.userdata": _b64(user),
        "guestinfo.userdata.encoding": "base64",
    }
