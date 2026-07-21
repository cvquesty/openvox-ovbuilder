"""
cloud-init guestinfo payloads for Packer golden-image clones.

=============================================================================
WHY
=============================================================================
After Terraform clones a golden template, the guest must learn its
per-instance identity without a console install:

  * hostname / FQDN
  * static IPv4 (address/prefix, gateway, DNS)

VMware Tools + cloud-init read ``guestinfo.metadata`` and
``guestinfo.userdata`` from the VM's extraConfig (set by Terraform).

CRITICAL — where network config must live
-----------------------------------------
The VMware datasource applies **network** from **metadata** (key
``network``, optional ``network.encoding``), not from user-data.

CRITICAL — route syntax on RHEL/Alma cloud-init
----------------------------------------------
Do **not** use netplan's ``to: default``. AlmaLinux/RHEL cloud-init's
network converter treats the string "default" as an IP and fails init-local:

  Address default is not a valid ip address
  Address default is not a valid ip network
  failed stage init-local

Use ``to: 0.0.0.0/0`` and also set ``gateway4`` for older renderers.

See: https://docs.cloud-init.io/en/latest/reference/datasources/vmware.html
"""

from __future__ import annotations

import base64
from typing import Optional


def _b64(s: str) -> str:
    """UTF-8 → base64 ASCII for guestinfo.* values."""
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def build_network_config(
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    domain: str = "",
) -> str:
    """
    Build cloud-init **Network Config Version 2** YAML (no top-level wrapper).

    Embedded under metadata ``network:`` for the VMware datasource.
    """
    gw = (gateway or "").strip()
    dns1 = (dns or "").strip()

    # No set-name: renaming to eth0 left Alma with an unbound
    # "cloud-init eth0" profile while the real NIC stayed ens33 on DHCP.
    lines = [
        "version: 2",
        "ethernets:",
        "  nics:",
        "    match:",
        '      name: "e*"',
        "    dhcp4: false",
        "    optional: true",
        "    addresses:",
        f"      - {ip}/{prefix}",
    ]
    if gw:
        # to: 0.0.0.0/0 — NOT "default" (Alma rejects default as an address).
        # Prefer routes only (gateway4 is deprecated in cloud-init 22.4+).
        lines += [
            "    routes:",
            "      - to: 0.0.0.0/0",
            f"        via: {gw}",
        ]
    if dns1:
        lines += [
            "    nameservers:",
            f"      addresses: [{dns1}]",
        ]
        if domain:
            lines.append(f"      search: [{domain}]")
    lines.append("")
    return "\n".join(lines)


def build_metadata(
    hostname: str,
    domain: str = "",
    *,
    ip: Optional[str] = None,
    prefix: Optional[int] = None,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
) -> str:
    """
    Build cloud-init **metadata** YAML (instance-id, hostname, **network**).

    Network Config v2 is embedded as base64 under ``network`` with
    ``network.encoding: base64`` (VMware guestinfo contract).
    """
    _ = domain
    lines = [
        f"instance-id: {hostname}",
        f"local-hostname: {hostname}",
        f"hostname: {hostname}",
    ]
    if ip is not None and prefix is not None:
        net_yaml = build_network_config(
            ip, prefix, gateway=gateway, dns=dns, domain=domain
        )
        lines += [
            f"network: {_b64(net_yaml)}",
            "network.encoding: base64",
        ]
    lines.append("")
    return "\n".join(lines)


def build_userdata(
    hostname: str,
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    domain: str = "",
) -> str:
    """
    Build cloud-init **user-data** YAML (hostname / hygiene only).

    Network is intentionally **not** here — see module docstring.
    """
    _ = (ip, prefix, gateway, dns)
    fqdn = f"{hostname}.{domain}" if domain else hostname

    lines = [
        "#cloud-config",
        f"hostname: {hostname}",
        f"fqdn: {fqdn}",
        "prefer_fqdn_over_hostname: true",
        "manage_etc_hosts: true",
        "package_update: false",
        "package_upgrade: false",
        # Avoid multi-minute boots when carrier/DHCP is wrong; static IP
        # comes from metadata network, not wait-online.
        "bootcmd:",
        "  - [systemctl, disable, --now, NetworkManager-wait-online.service]",
        "runcmd:",
        f"  - [hostnamectl, set-hostname, {hostname}]",
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
) -> dict:
    """
    Return a dict for Terraform ``extra_config`` / ``guestinfo_extra_config``.

    Keys
    ----
    guestinfo.metadata / .encoding   — identity + network (VMware path)
    guestinfo.userdata / .encoding   — hostname hygiene only

    Deliberately **no** guestinfo.networkconfig: shipping network in both
    metadata and networkconfig can double-apply or confuse older cloud-init.
    """
    meta = build_metadata(
        hostname,
        domain=domain,
        ip=ip,
        prefix=prefix,
        gateway=gateway,
        dns=dns,
    )
    user = build_userdata(
        hostname, ip, prefix, gateway=gateway, dns=dns, domain=domain
    )
    return {
        "guestinfo.metadata": _b64(meta),
        "guestinfo.metadata.encoding": "base64",
        "guestinfo.userdata": _b64(user),
        "guestinfo.userdata.encoding": "base64",
    }
