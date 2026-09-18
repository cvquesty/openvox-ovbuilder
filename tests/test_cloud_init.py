"""Unit tests for cloud-init guestinfo helpers."""

from __future__ import annotations

import base64
import os

from ovbuilder.cloud_init import (
    build_metadata,
    build_userdata,
    guestinfo_extra_config,
    _network_configure_script,
)
from ovbuilder.openvox_site import DEFAULT_SITES

from tests.placeholders import placeholder_value


def test_metadata_is_identity_only():
    m = build_metadata("web1", domain="example.com")
    assert "instance-id: web1" in m
    assert "network:" not in m


def test_network_script_has_netplan_and_nmcli_paths():
    s = _network_configure_script(
        "10.0.1.5", 24, gateway="10.0.1.1", dns="10.0.1.2", domain="lab.local"
    )
    assert "10.0.1.5/24" in s
    assert "path=netplan" in s
    assert "99-ovbuilder.yaml" in s
    assert "path=nmcli" in s
    assert 'connection.id "$IFACE"' in s
    assert "10.0.1.2" in s


def test_network_script_multiple_dns():
    s = _network_configure_script(
        "10.0.1.5",
        24,
        gateway="10.0.1.1",
        dns=["8.8.8.8", "1.1.1.1"],
        domain="lab.local",
    )
    assert "8.8.8.8, 1.1.1.1" in s  # netplan CSV
    assert "8.8.8.8 1.1.1.1" in s  # nmcli space-separated


def test_userdata_without_password_omits_chpasswd():
    u = build_userdata(
        "web1", "10.0.1.5", 24, gateway="10.0.1.1", password=None
    )
    assert "config: disabled" in u
    assert "chpasswd:" not in u
    assert "ovbuilder-net.sh" in u
    # No groups passed → no DNF script.
    assert "ovbuilder-dnf-groups.sh" not in u


def test_userdata_embeds_dnf_groups_script():
    u = build_userdata(
        "web1",
        "10.0.1.5",
        24,
        password=None,
        dnf_groups=["Development Tools", "Server"],
    )
    assert "ovbuilder-dnf-groups.sh" in u
    assert "Development Tools" in u
    assert "Server" in u
    # Runs after network script in runcmd.
    net_pos = u.find("/usr/local/sbin/ovbuilder-net.sh")
    dnf_pos = u.find("/usr/local/sbin/ovbuilder-dnf-groups.sh")
    assert net_pos != -1 and dnf_pos != -1
    assert dnf_pos > net_pos


def test_userdata_embeds_proxy_and_agent_after_network():
    u = build_userdata(
        "web1",
        "10.0.1.5",
        24,
        domain="atlc-it.corp.int-x.ai",
        password=None,
        http_proxy="http://proxy.example.com:3128",
        openvox_site=DEFAULT_SITES["ATLC"],
    )
    assert "ovbuilder-proxy.sh" in u
    assert "ovbuilder-openvox-agent.sh" in u
    assert "proxy.example.com:3128" in u
    assert "ovcompilers.atlc-it.corp.int-x.ai" in u
    assert "--ca-server ovca.corp.int-x.ai" in u
    net = u.find("/usr/local/sbin/ovbuilder-net.sh")
    proxy = u.rfind("/usr/local/sbin/ovbuilder-proxy.sh")
    agent = u.rfind("/usr/local/sbin/ovbuilder-openvox-agent.sh")
    assert net != -1 and proxy != -1 and agent != -1
    assert net < proxy < agent


def test_userdata_with_password_includes_chpasswd():
    guest = placeholder_value()
    u = build_userdata(
        "web1",
        "10.0.1.5",
        24,
        default_user="ubuntu",
        password=guest,
    )
    assert f"ubuntu:{guest}" in u
    assert f"root:{guest}" in u


def test_guestinfo_uses_env_password(monkeypatch=None):
    # stdlib-friendly: set env without pytest monkeypatch
    key = "OVBUILDER_GOLDEN_PASSWORD"
    old = os.environ.get(key)
    guest = placeholder_value()
    os.environ[key] = guest
    try:
        g = guestinfo_extra_config(
            "n1", "192.168.1.10", 19, gateway="192.168.1.1", default_user="ubuntu"
        )
        user = base64.b64decode(g["guestinfo.userdata"]).decode()
        assert guest in user
        # Password content comes only from env/secrets, never a module default.
        assert "GOLDEN_DEFAULT_PASSWORD" not in user
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
