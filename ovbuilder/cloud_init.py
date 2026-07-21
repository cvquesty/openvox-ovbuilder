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

Network matching uses ``name: "e*"`` so both ``eth0`` and ``ens192`` style
interface names work across AlmaLinux and Ubuntu goldens.
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


def build_metadata(hostname: str, domain: str = "") -> str:
    """
    Build cloud-init **metadata** YAML (instance-id + local-hostname).

    ``domain`` is accepted for API symmetry with userdata; metadata only
    needs a stable instance-id (we use the short hostname).
    """
    # domain intentionally unused in metadata body — reserved for future
    # availability-zone style fields if needed.
    _ = domain
    # Trailing newline keeps YAML parsers happy.
    return (
        f"instance-id: {hostname}\n"
        f"local-hostname: {hostname}\n"
        f"hostname: {hostname}\n"
    )


def build_userdata(
    hostname: str,
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[str] = None,
    domain: str = "",
) -> str:
    """
    Build cloud-init **user-data** YAML including netplan-compatible network.

    Parameters
    ----------
    hostname : short host name (also used by runcmd hostnamectl)
    ip : IPv4 address (no mask)
    prefix : CIDR length 0–32 (from parse_cidr_prefix)
    gateway, dns : optional; omitted from YAML if blank
    domain : used for FQDN and optional DNS search list

    Design choices
    --------------
    * ``package_update/upgrade: false`` — goldens are pre-updated; first boot
      must be fast and offline-friendly.
    * Network match ``e*`` — covers eth* and ens*.
    * No users:[] mutation of baked Packer accounts (almalinux / ubuntu).
    """
    fqdn = f"{hostname}.{domain}" if domain else hostname
    gw = (gateway or "").strip()
    dns1 = (dns or "").strip()

    # Build YAML line-by-line so optional route/DNS blocks stay correctly indented.
    lines = [
        "#cloud-config",
        f"hostname: {hostname}",
        f"fqdn: {fqdn}",
        "prefer_fqdn_over_hostname: true",
        "manage_etc_hosts: true",
        "package_update: false",
        "package_upgrade: false",
        "network:",
        "  version: 2",
        "  ethernets:",
        "    nics:",
        "      match:",
        '        name: "e*"',
        "      dhcp4: false",
        "      addresses:",
        f"        - {ip}/{prefix}",
    ]
    if gw:
        # Default route only when the operator supplied a gateway.
        lines += [
            "      routes:",
            "        - to: default",
            f"          via: {gw}",
        ]
    if dns1:
        lines += [
            "      nameservers:",
            f"        addresses: [{dns1}]",
        ]
        if domain:
            lines.append(f"        search: [{domain}]")

    lines += [
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
    guestinfo.metadata           — base64 metadata YAML
    guestinfo.metadata.encoding  — "base64"
    guestinfo.userdata           — base64 user-data YAML
    guestinfo.userdata.encoding  — "base64"
    """
    meta = build_metadata(hostname, domain=domain)
    user = build_userdata(
        hostname, ip, prefix, gateway=gateway, dns=dns, domain=domain
    )
    return {
        "guestinfo.metadata": _b64(meta),
        "guestinfo.metadata.encoding": "base64",
        "guestinfo.userdata": _b64(user),
        "guestinfo.userdata.encoding": "base64",
    }
