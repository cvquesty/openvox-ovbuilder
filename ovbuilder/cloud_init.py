"""
Generate cloud-init guestinfo payloads for golden-image clones.

ovbuilder injects these via vSphere extra_config so first boot sets
hostname + static IPv4 without console or post-SSH network hacks.
"""

from __future__ import annotations

import base64
import textwrap
from typing import Optional


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def build_metadata(hostname: str, domain: str = "") -> str:
    """cloud-init metadata (YAML) for guestinfo.metadata."""
    return textwrap.dedent(
        f"""\
        instance-id: {hostname}
        local-hostname: {hostname}
        hostname: {hostname}
        """
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
    cloud-init user-data (YAML) including network config.

    Matches any Ethernet interface whose name starts with 'e' (eth0, ens192, …).
    """
    fqdn = f"{hostname}.{domain}" if domain else hostname
    gw = (gateway or "").strip()
    dns1 = (dns or "").strip()

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
    Return dict suitable for vsphere_virtual_machine.extra_config
    (guestinfo.* keys with base64 payloads).
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
