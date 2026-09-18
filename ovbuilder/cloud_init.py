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

Hostname still comes from metadata + user-data. SSH password auth is
**off** unless the operator opts in; root stays locked. Prefer injecting
``ssh_authorized_keys`` for clone access.
"""

from __future__ import annotations

import base64
import json
import shlex
from typing import Optional, Sequence, Union

from .network import normalize_dns_servers
from .openvox_site import (
    OpenVoxSite,
    agent_install_command,
    no_proxy_csv,
    parse_proxy_url,
)
from .packages import build_dnf_groupinstall_script, sanitize_dnf_groups
from .secrets import (
    allow_password_ssh as password_ssh_opted_in,
    get_golden_password,
    get_http_proxy,
    get_ssh_authorized_keys_raw,
    require_golden_password,
)

# Public key prefixes only — private-key PEM is rejected in normalize().
_SSH_PUBKEY_PREFIXES = (
    "ssh-ed25519 ",
    "ssh-ed25519-cert-v01@",
    "ssh-rsa ",
    "ssh-rsa-cert-v01@",
    "ecdsa-sha2-nistp256 ",
    "ecdsa-sha2-nistp384 ",
    "ecdsa-sha2-nistp521 ",
    "ecdsa-sha2-nistp256-cert-v01@",
    "ecdsa-sha2-nistp384-cert-v01@",
    "ecdsa-sha2-nistp521-cert-v01@",
    "sk-ssh-ed25519@",
    "sk-ecdsa-sha2-nistp256@",
)


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
    dns: Optional[Union[str, Sequence[str]]] = None,
    domain: str = "",
) -> str:
    """
    Bash body (no shebang): configure primary e* NIC via netplan or nmcli.
    """
    gw = (gateway or "").strip()
    dns_list = normalize_dns_servers(dns)
    # Netplan wants comma list; nmcli wants space-separated DNS values.
    dns_csv = ", ".join(dns_list)
    dns_nm = " ".join(dns_list)
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
    if dns_csv:
        nameservers_block = (
            "      nameservers:\n"
            f"        addresses: [{dns_csv}]\n"
        )
        if dom:
            nameservers_block += f"        search: [{dom}]\n"

    # nmcli optional modify args
    extra_mod: list[str] = []
    if gw:
        extra_mod.append(f"ipv4.gateway {shlex.quote(gw)}")
    else:
        extra_mod.append("ipv4.gateway ''")
    if dns_nm:
        extra_mod.append(f"ipv4.dns {shlex.quote(dns_nm)}")
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
    dns: Optional[Union[str, Sequence[str]]] = None,
    domain: str = "",
) -> str:
    return _network_configure_script(
        ip, prefix, gateway=gateway, dns=dns, domain=domain
    )


def build_proxy_apply_script(
    proxy_url: str,
    no_proxy: str,
) -> str:
    """
    Bash that writes apt/dnf/environment/profile.d proxy settings.

    ``proxy_url`` is written only into root-owned files on the guest.
    """
    parsed = parse_proxy_url(proxy_url)
    url = parsed.get("url") or proxy_url
    host = parsed.get("host") or ""
    port = parsed.get("port") or 3128
    user = parsed.get("username") or ""
    pw = parsed.get("password") or ""
    url_q = shlex.quote(url)
    np_q = shlex.quote(no_proxy)
    user_q = shlex.quote(user)
    pw_q = shlex.quote(pw)
    host_q = shlex.quote(str(host))
    port_q = shlex.quote(str(port))
    dnf_user = f"echo proxy_username={user_q}" if user else "true"
    dnf_pw = f"echo proxy_password={pw_q}" if pw else "true"
    return f"""#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /etc/profile.d /etc/apt/apt.conf.d /etc/dnf
printf '%s\\n' \\
  'export http_proxy={url_q}' \\
  'export https_proxy={url_q}' \\
  'export HTTP_PROXY={url_q}' \\
  'export HTTPS_PROXY={url_q}' \\
  'export no_proxy={np_q}' \\
  'export NO_PROXY={np_q}' \\
  > /etc/profile.d/ovbuilder-proxy.sh
chmod 0644 /etc/profile.d/ovbuilder-proxy.sh
touch /etc/environment
for key in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY; do
  grep -q "^${{key}}=" /etc/environment && sed -i "/^${{key}}=/d" /etc/environment || true
done
printf '%s\\n' \\
  'http_proxy={url_q}' \\
  'https_proxy={url_q}' \\
  'HTTP_PROXY={url_q}' \\
  'HTTPS_PROXY={url_q}' \\
  'no_proxy={np_q}' \\
  'NO_PROXY={np_q}' \\
  >> /etc/environment
if [ -d /etc/apt/apt.conf.d ]; then
  printf '%s\\n' \\
    'Acquire::http::Proxy "{url}";' \\
    'Acquire::https::Proxy "{url}";' \\
    > /etc/apt/apt.conf.d/01ovbuilder-proxy
  chmod 0644 /etc/apt/apt.conf.d/01ovbuilder-proxy
fi
if command -v dnf >/dev/null 2>&1 || [ -f /etc/dnf/dnf.conf ]; then
  mkdir -p /etc/dnf/dnf.conf.d
  {{
    echo '[main]'
    echo proxy=http://{host_q}:{port_q}
    {dnf_user}
    {dnf_pw}
  }} > /etc/dnf/dnf.conf.d/ovbuilder-proxy.conf
  chmod 0600 /etc/dnf/dnf.conf.d/ovbuilder-proxy.conf
fi
"""


def normalize_ssh_authorized_keys(
    keys: Optional[Union[str, Sequence[str]]] = None,
) -> list[str]:
    """
    Return unique SSH **public** keys. Private-key material is dropped.
    """
    if keys is None:
        return []
    if isinstance(keys, str):
        raw_lines = keys.replace("\r\n", "\n").replace("\\n", "\n").split("\n")
    else:
        raw_lines: list[str] = []
        for item in keys:
            if item is None:
                continue
            raw_lines.extend(str(item).replace("\r\n", "\n").split("\n"))
    out: list[str] = []
    seen: set[str] = set()
    skip_block = False
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            skip_block = False
            continue
        upper = line.upper()
        if "PRIVATE KEY" in upper or line.startswith("-----BEGIN"):
            skip_block = True
            continue
        if skip_block:
            if line.startswith("-----END"):
                skip_block = False
            continue
        if line.startswith("#"):
            continue
        if not line.startswith(_SSH_PUBKEY_PREFIXES):
            continue
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


def build_agent_install_script(site: OpenVoxSite) -> str:
    """Bash wrapper around the GUI install.bash clustered flags."""
    cmd = agent_install_command(site)
    return f"""#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
{cmd}
"""


def build_userdata(
    hostname: str,
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[Union[str, Sequence[str]]] = None,
    domain: str = "",
    default_user: str = "ubuntu",
    password: Optional[str] = None,
    dnf_groups: Optional[list] = None,
    http_proxy: Optional[str] = None,
    openvox_site: Optional[OpenVoxSite] = None,
    allow_password_ssh: bool = False,
    ssh_authorized_keys: Optional[Union[str, Sequence[str]]] = None,
) -> str:
    """
    Build cloud-init **user-data**: hostname, net apply, optional SSH keys.

    Defaults keep Packer golden hardening:

    * ``ssh_pwauth: false`` (key-based SSH)
    * ``disable_root: true`` (root stays locked; never in chpasswd)
    * no password injection unless ``allow_password_ssh`` and ``password``

    ``password`` must come from the operator (env / secrets.env). It is never
    a hard-coded default. Root is never unlocked.
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
    pw = (password or "").strip() if allow_password_ssh else ""
    keys = normalize_ssh_authorized_keys(ssh_authorized_keys)
    groups = sanitize_dnf_groups(dnf_groups)
    dnf_script = build_dnf_groupinstall_script(groups) if groups else ""
    dnf_block = ""
    if dnf_script:
        dnf_block = "\n".join(
            f"      {line}" if line else "      "
            for line in dnf_script.splitlines()
        )

    lines = [
        "#cloud-config",
        f"hostname: {hostname}",
        f"fqdn: {fqdn}",
        "prefer_fqdn_over_hostname: true",
        "manage_etc_hosts: true",
        "package_update: false",
        "package_upgrade: false",
        f"ssh_pwauth: {'true' if pw else 'false'}",
        "disable_root: true",
    ]
    if keys:
        lines.append("ssh_authorized_keys:")
        for key in keys:
            lines.append(f"  - {json.dumps(key)}")
    if pw:
        # Password never hard-coded in the repo — supplied at runtime only.
        # Default user only; root stays locked (no root chpasswd).
        lines += [
            "chpasswd:",
            "  expire: false",
            "  list: |",
            f"    {user}:{pw}",
        ]
    lines += [
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
    ]
    if dnf_block:
        lines += [
            "  - path: /usr/local/sbin/ovbuilder-dnf-groups.sh",
            "    permissions: '0755'",
            "    owner: root:root",
            "    content: |",
            dnf_block,
        ]
    extras = [fqdn, ip]
    proxy_url = (http_proxy or "").strip()
    site = openvox_site
    if proxy_url:
        np = no_proxy_csv(extra=extras, site=site)
        proxy_script = build_proxy_apply_script(proxy_url, np)
        proxy_block = "\n".join(
            f"      {line}" if line else "      "
            for line in proxy_script.splitlines()
        )
        lines += [
            "  - path: /usr/local/sbin/ovbuilder-proxy.sh",
            "    permissions: '0755'",
            "    owner: root:root",
            "    content: |",
            proxy_block,
        ]
    if site:
        agent_script = build_agent_install_script(site)
        agent_block = "\n".join(
            f"      {line}" if line else "      "
            for line in agent_script.splitlines()
        )
        lines += [
            "  - path: /usr/local/sbin/ovbuilder-openvox-agent.sh",
            "    permissions: '0755'",
            "    owner: root:root",
            "    content: |",
            agent_block,
        ]
    lines += [
        "runcmd:",
        f"  - [hostnamectl, set-hostname, {hostname}]",
    ]
    if pw:
        # User only. Root is never unlocked at clone time.
        safe_user = user.replace("'", "")
        safe_pw = pw.replace("'", "'\\''")
        lines.append(
            f"  - [bash, -c, \"echo '{safe_user}:{safe_pw}' | chpasswd\"]"
        )
    lines += [
        "  - [/usr/local/sbin/ovbuilder-net.sh]",
    ]
    if proxy_url:
        lines.append("  - [/usr/local/sbin/ovbuilder-proxy.sh]")
    if dnf_block:
        # After network + proxy so base/appstream repos resolve.
        lines.append("  - [/usr/local/sbin/ovbuilder-dnf-groups.sh]")
    if site:
        lines.append("  - [/usr/local/sbin/ovbuilder-openvox-agent.sh]")
    lines += [
        f'final_message: "ovbuilder cloud-init finished for {hostname}"',
        "",
    ]
    return "\n".join(lines)


def guestinfo_extra_config(
    hostname: str,
    ip: str,
    prefix: int,
    gateway: Optional[str] = None,
    dns: Optional[Union[str, Sequence[str]]] = None,
    domain: str = "",
    default_user: str = "ubuntu",
    password: Optional[str] = None,
    dnf_groups: Optional[list] = None,
    http_proxy: Optional[str] = None,
    openvox_site: Optional[OpenVoxSite] = None,
    allow_password_ssh: Optional[bool] = None,
    ssh_authorized_keys: Optional[Union[str, Sequence[str]]] = None,
    *,
    require_password: Optional[bool] = None,
) -> dict:
    """
    Return a dict for Terraform ``extra_config`` / ``guestinfo_extra_config``.

    Password SSH is off unless ``allow_password_ssh`` (or
    ``OVBUILDER_ALLOW_PASSWORD_SSH``). When opted in, the password comes from
    ``password`` / local secrets / env — never a default in source control.
    SSH public keys come from ``ssh_authorized_keys`` or operator key files.
    """
    allow = (
        allow_password_ssh
        if allow_password_ssh is not None
        else password_ssh_opted_in()
    )
    if ssh_authorized_keys is None:
        keys: Optional[Union[str, Sequence[str]]] = get_ssh_authorized_keys_raw()
    else:
        keys = ssh_authorized_keys
    pw = ""
    if allow:
        pw = (password or "").strip() or (get_golden_password() or "")
        need_pw = True if require_password is None else require_password
        if need_pw and not pw:
            pw = require_golden_password("golden clone guestinfo")
    proxy = (http_proxy or "").strip() or (get_http_proxy() or "")
    meta = build_metadata(hostname, domain=domain)
    user = build_userdata(
        hostname,
        ip,
        prefix,
        gateway=gateway,
        dns=dns,
        domain=domain,
        default_user=default_user,
        password=pw or None,
        dnf_groups=dnf_groups,
        http_proxy=proxy or None,
        openvox_site=openvox_site,
        allow_password_ssh=allow,
        ssh_authorized_keys=keys,
    )
    return {
        "guestinfo.metadata": _b64(meta),
        "guestinfo.metadata.encoding": "base64",
        "guestinfo.userdata": _b64(user),
        "guestinfo.userdata.encoding": "base64",
    }
