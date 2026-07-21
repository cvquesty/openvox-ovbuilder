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
This module builds those YAML documents and base64-encodes them the way
the VMware datasource expects (encoding keys = "base64").

CRITICAL — where network config must live
-----------------------------------------
The VMware datasource applies **network** from **metadata** (key
``network``, optional ``network.encoding``), not from a top-level
``network:`` block in user-data.

Putting netplan-style config only in user-data is a common mistake:
cloud-init still runs (hostname, runcmd, NM profile rename like
"cloud-init ens33") but the interface never gets the static IP.

See: https://docs.cloud-init.io/en/latest/reference/datasources/vmware.html
     "Configuring the network"

Network matching uses ``name: "e*"`` so both ``eth0`` and ``ens192`` /
``ens33`` style names work across AlmaLinux and Ubuntu goldens.
"""

from __future__ import annotations

import base64
from typing import Optional


def _b64(s: str) -> str:
    """
    UTF-8 → base64 ASCII for guestinfo.* values.

    vSphere extraConfig values are strings; base64 avoids quoting issues
    with newlines and special characters in YAML.
    """
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

    This document is embedded under metadata ``network:`` for the VMware
    datasource (and may also be set as guestinfo.networkconfig).
    """
    gw = (gateway or "").strip()
    dns1 = (dns or "").strip()

    lines = [
        "version: 2",
        "ethernets:",
        "  nics:",
        "    match:",
        '      name: "e*"',
        "    dhcp4: false",
        "    addresses:",
        f"      - {ip}/{prefix}",
    ]
    if gw:
        lines += [
            "    routes:",
            "      - to: default",
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

    When ``ip`` and ``prefix`` are set, embeds Network Config v2 under the
    ``network`` key (base64 + ``network.encoding``) — required for VMware
    guestinfo static networking.
    """
    _ = domain  # reserved for future az / region style fields
    lines = [
        f"instance-id: {hostname}",
        f"local-hostname: {hostname}",
        f"hostname: {hostname}",
    ]
    if ip is not None and prefix is not None:
        net_yaml = build_network_config(
            ip, prefix, gateway=gateway, dns=dns, domain=domain
        )
        # VMware datasource: metadata.network may be encoded (base64).
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

    Network is intentionally **not** here — see module docstring and
    ``build_metadata``. ``ip``/``prefix``/``gateway``/``dns`` are accepted
    for call-site symmetry with older APIs but are unused in this document.
    """
    _ = (ip, prefix, gateway, dns)  # network lives in metadata
    fqdn = f"{hostname}.{domain}" if domain else hostname

    lines = [
        "#cloud-config",
        f"hostname: {hostname}",
        f"fqdn: {fqdn}",
        "prefer_fqdn_over_hostname: true",
        "manage_etc_hosts: true",
        "package_update: false",
        "package_upgrade: false",
        "runcmd:",
        # Belt-and-suspenders: some images set hostname late; force it.
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

    Keys (VMware cloud-init datasource contract)
    --------------------------------------------
    guestinfo.metadata            — base64 metadata YAML (includes network)
    guestinfo.metadata.encoding   — "base64"
    guestinfo.userdata            — base64 user-data YAML (hostname, etc.)
    guestinfo.userdata.encoding   — "base64"
    guestinfo.networkconfig       — base64 Network Config v2 (belt-and-suspenders)
    guestinfo.networkconfig.encoding — "base64"
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
    net = build_network_config(
        ip, prefix, gateway=gateway, dns=dns, domain=domain
    )
    return {
        "guestinfo.metadata": _b64(meta),
        "guestinfo.metadata.encoding": "base64",
        "guestinfo.userdata": _b64(user),
        "guestinfo.userdata.encoding": "base64",
        # Some cloud-init builds also honor a dedicated networkconfig key.
        "guestinfo.networkconfig": _b64(net),
        "guestinfo.networkconfig.encoding": "base64",
    }
