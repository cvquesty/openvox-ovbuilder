"""Unit tests for cloud-init guestinfo helpers."""

from __future__ import annotations

import base64

from ovbuilder.cloud_init import (
    build_metadata,
    build_userdata,
    guestinfo_extra_config,
    _network_configure_script,
)


def test_metadata_is_identity_only():
    m = build_metadata("web1", domain="example.com")
    assert "instance-id: web1" in m
    assert "local-hostname: web1" in m
    assert "network:" not in m


def test_network_script_has_netplan_and_nmcli_paths():
    s = _network_configure_script(
        "10.0.1.5", 24, gateway="10.0.1.1", dns="10.0.1.2", domain="lab.local"
    )
    assert "10.0.1.5/24" in s
    # Ubuntu path
    assert "path=netplan" in s
    assert "99-ovbuilder.yaml" in s
    assert "netplan apply" in s
    assert "to: 0.0.0.0/0" in s
    # Alma path
    assert "path=nmcli" in s
    assert 'connection.id "$IFACE"' in s
    assert "deleting spare profile" in s


def test_userdata_disables_ci_network_and_embeds_script():
    u = build_userdata(
        "web1",
        "10.0.1.5",
        24,
        gateway="10.0.1.1",
        dns="10.0.1.2",
        domain="lab",
        default_user="ubuntu",
    )
    assert "hostname: web1" in u
    assert "config: disabled" in u
    assert "/usr/local/sbin/ovbuilder-net.sh" in u
    assert "10.0.1.5/24" in u
    assert "netplan apply" in u
    assert "ubuntu:ChangeMe-BuildOnly!" in u
    assert "root:ChangeMe-BuildOnly!" in u


def test_guestinfo_payloads():
    g = guestinfo_extra_config(
        "n1",
        "192.168.1.10",
        19,
        gateway="192.168.1.1",
        dns="1.1.1.1",
        default_user="ubuntu",
    )
    assert "guestinfo.networkconfig" not in g
    meta = base64.b64decode(g["guestinfo.metadata"]).decode()
    assert "network:" not in meta
    user = base64.b64decode(g["guestinfo.userdata"]).decode()
    assert "config: disabled" in user
    assert "192.168.1.10/19" in user
    assert "99-ovbuilder.yaml" in user
