"""Unit tests for cloud-init guestinfo helpers."""

from __future__ import annotations

import base64

from ovbuilder.cloud_init import (
    build_metadata,
    build_userdata,
    guestinfo_extra_config,
    _nmcli_configure_script,
)


def test_metadata_is_identity_only():
    m = build_metadata("web1", domain="example.com")
    assert "instance-id: web1" in m
    assert "local-hostname: web1" in m
    assert "network:" not in m


def test_nmcli_script_reuses_existing_connection():
    s = _nmcli_configure_script(
        "10.0.1.5", 24, gateway="10.0.1.1", dns="10.0.1.2", domain="lab.local"
    )
    assert "10.0.1.5/24" in s
    assert "connection.id" in s
    assert "connection.interface-name" in s
    assert "ipv4.method manual" in s
    assert "deleting spare profile" in s
    # Profile must end up named exactly after the iface (ens33), never
    # "cloud-init ens33"
    assert 'connection.id "$IFACE"' in s
    assert "match:" not in s


def test_userdata_disables_cloud_init_network_and_runs_nmcli():
    u = build_userdata(
        "web1", "10.0.1.5", 24, gateway="10.0.1.1", dns="10.0.1.2", domain="lab"
    )
    assert "hostname: web1" in u
    assert "config: disabled" in u
    assert "/usr/local/sbin/ovbuilder-net.sh" in u
    assert "10.0.1.5/24" in u
    assert "NetworkManager-wait-online" in u
    # No Network Config v2 "ethernets/nics/match" path
    assert "ethernets:" not in u
    assert 'name: "e*"' not in u


def test_guestinfo_payloads():
    g = guestinfo_extra_config(
        "n1", "192.168.1.10", 19, gateway="192.168.1.1", dns="1.1.1.1"
    )
    assert g["guestinfo.metadata.encoding"] == "base64"
    assert g["guestinfo.userdata.encoding"] == "base64"
    assert "guestinfo.networkconfig" not in g

    meta = base64.b64decode(g["guestinfo.metadata"]).decode()
    assert "network:" not in meta
    assert "instance-id: n1" in meta

    user = base64.b64decode(g["guestinfo.userdata"]).decode()
    assert "config: disabled" in user
    assert "192.168.1.10/19" in user
    assert "ovbuilder-net.sh" in user
