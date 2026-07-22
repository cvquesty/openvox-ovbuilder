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
NETWORK STRATEGY
=============================================================================
cloud-init's own Network Config v2 renderer is **disabled**. It creates
orphan NM profiles (``cloud-init nics``) or leaves Ubuntu netplan with
``dhcp4: false`` and no addresses (golden autoinstall hygiene).

Clone-time ``/usr/local/sbin/ovbuilder-net.sh`` applies interview data:

  * **Ubuntu** (``/etc/netplan`` present): write ``99-ovbuilder.yaml`` for the
    real iface name (e.g. ens33), remove conflicting netplan files, ``netplan apply``.
  * **Alma/RHEL** (NetworkManager / nmcli): modify the existing connection
    **in place**, rename profile to the iface name only (``ens33``, not
    ``cloud-init ens33``), delete spare ethernet profiles, bring it up.
  * **Fallback**: ``ip addr`` / ``ip route`` if neither stack is available.

Hostname / passwords still come from metadata + user-data as usual.
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

    No network block — network is applied via ovbuilder-net.sh in runcmd.
    """
    _ = domain
    return (
        f"instance-id: {hostname}\n"
        f"local-hostname: {hostname}\n"
        f"hostname: {hostname}\n"
    )


def _network_configure_script(
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    domain: str = "",
) -> str:
    """
    Bash body (no shebang): configure primary e* NIC via netplan or nmcli.
    """
    gw = (gateway or "").strip()
    dns1 = (dns or "").strip()
    dom = (domain or "").strip()
    ip_cidr = f"{ip}/{prefix}"

    # Netplan optional blocks (Python-expanded constants; IFACE is bash runtime).
    routes_block = ""
    if gw:
        routes_block = (
            "      routes:\n"
            "        - to: 0.0.0.0/0\n"
            f"          via: {gw}\n"
        )
    nameservers_block = ""
    if dns1:
        nameservers_block = (
            "      nameservers:\n"
            f"        addresses: [{dns1}]\n"
        )
        if dom:
            nameservers_block += f"        search: [{dom}]\n"

    # nmcli optional modify args
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
    nm_extra = " ".join(extra_mod)
    ip_cidr_q = shlex.quote(ip_cidr)

    return f"""
set +e
# Primary ethernet: first e* device (ens33, ens192, eth0, …)
IFACE=$(ls -1 /sys/class/net 2>/dev/null | grep -E '^e' | head -1)
if [ -z "$IFACE" ]; then
  echo "ovbuilder-net: no ethernet interface found" >&2
  exit 1
fi
echo "ovbuilder-net: primary iface=$IFACE ip={ip_cidr}"

# ---------------------------------------------------------------------------
# Ubuntu: netplan owns addressing (systemd-networkd). Golden autoinstall left
# dhcp4:false with no addresses — ip addr stays empty until we write netplan.
# ---------------------------------------------------------------------------
if [ -d /etc/netplan ] && command -v netplan >/dev/null 2>&1; then
  echo "ovbuilder-net: path=netplan"
  for f in /etc/netplan/*.yaml /etc/netplan/*.yml; do
    [ -f "$f" ] || continue
    case "$f" in
      */99-ovbuilder.yaml) continue ;;
    esac
    echo "ovbuilder-net: removing conflicting $f"
    rm -f "$f"
  done
  cat > /etc/netplan/99-ovbuilder.yaml <<EOF
network:
  version: 2
  ethernets:
    $IFACE:
      dhcp4: false
      dhcp6: false
      addresses:
        - {ip_cidr}
{routes_block}{nameservers_block}EOF
  chmod 600 /etc/netplan/99-ovbuilder.yaml
  echo "ovbuilder-net: wrote /etc/netplan/99-ovbuilder.yaml"
  cat /etc/netplan/99-ovbuilder.yaml
  netplan generate 2>/dev/null || true
  netplan apply
  sleep 1
  ip -br addr show "$IFACE" || true
  ip route | head -10 || true
  exit 0
fi

# ---------------------------------------------------------------------------
# Alma / RHEL: NetworkManager — edit existing profile in place, name = iface
# ---------------------------------------------------------------------------
if command -v nmcli >/dev/null 2>&1; then
  echo "ovbuilder-net: path=nmcli"
  CONN=$(nmcli -t -f NAME,DEVICE connection show 2>/dev/null \\
    | awk -F: -v i="$IFACE" '$2 == i {{ print $1; exit }}')
  if [ -z "$CONN" ]; then
    CONN=$(nmcli -t -f NAME,TYPE connection show 2>/dev/null \\
      | awk -F: '$2 == "802-3-ethernet" {{ print $1; exit }}')
  fi
  if [ -z "$CONN" ]; then
    nmcli connection add type ethernet ifname "$IFACE" con-name "$IFACE" \\
      ipv4.method manual ipv6.method ignore
    CONN="$IFACE"
    echo "ovbuilder-net: created connection $CONN"
  else
    echo "ovbuilder-net: found connection '$CONN' — renaming to $IFACE"
  fi
  nmcli connection modify "$CONN" \\
    connection.id "$IFACE" \\
    connection.interface-name "$IFACE" \\
    connection.autoconnect yes \\
    connection.autoconnect-priority 100 \\
    ipv4.method manual \\
    ipv4.addresses {ip_cidr_q} \\
    ipv6.method ignore \\
    {nm_extra}
  CONN="$IFACE"
  nmcli -t -f NAME,TYPE connection show 2>/dev/null | while IFS=: read -r name typ; do
    [ "$typ" = "802-3-ethernet" ] || continue
    [ "$name" = "$CONN" ] && continue
    echo "ovbuilder-net: deleting spare profile '$name'"
    nmcli connection delete "$name" 2>/dev/null || true
  done
  nmcli connection up "$CONN" || nmcli device reapply "$IFACE" || true
  ip -br addr show "$IFACE" || true
  exit 0
fi

# ---------------------------------------------------------------------------
# Last resort: raw iproute2
# ---------------------------------------------------------------------------
echo "ovbuilder-net: path=iproute2"
ip link set "$IFACE" up || true
ip addr flush dev "$IFACE" 2>/dev/null || true
ip addr add {ip_cidr_q} dev "$IFACE" || true
{("ip route replace default via " + shlex.quote(gw) + ' || true') if gw else "true"}
ip -br addr show "$IFACE" || true
""".strip()


# Back-compat alias for tests / imports
def _nmcli_configure_script(
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    domain: str = "",
) -> str:
    return _network_configure_script(
        ip, prefix, gateway=gateway, dns=dns, domain=domain
    )


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
    Build cloud-init **user-data**: hostname, passwords, disable CI network, apply IP.
    """
    fqdn = f"{hostname}.{domain}" if domain else hostname
    script = _network_configure_script(
        ip, prefix, gateway=gateway, dns=dns, domain=domain
    )
    body_lines = ["#!/bin/bash", *script.splitlines()]
    script_block = "\n".join(
        f"      {line}" if line else "      " for line in body_lines
    )
    user = (default_user or "ubuntu").strip() or "ubuntu"
    pw = password or GOLDEN_DEFAULT_PASSWORD

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
        "chpasswd:",
        "  expire: false",
        "  list: |",
        f"    {user}:{pw}",
        f"    root:{pw}",
        "# Guest OS network is applied by ovbuilder-net.sh (netplan or nmcli).",
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
        f"  - [bash, -c, \"echo '{user}:{pw}' | chpasswd; echo 'root:{pw}' | chpasswd\"]",
        "  - [/usr/local/sbin/ovbuilder-net.sh]",
        f'final_message: "ovbuilder cloud-init finished for {hostname}"',
        "",
    ]
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
