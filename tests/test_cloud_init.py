"""Unit tests for cloud-init guestinfo helpers."""

from __future__ import annotations

import base64

from ovbuilder.cloud_init import (
    build_metadata,
    build_network_config,
    build_userdata,
    guestinfo_extra_config,
)


def test_network_config_static():
    n = build_network_config(
        "10.0.1.5", 24, gateway="10.0.1.1", dns="10.0.1.2", domain="lab.local"
    )
    assert "10.0.1.5/24" in n
    assert "via: 10.0.1.1" in n
    assert 'name: "e*"' in n
    assert "10.0.1.2" in n
    assert "lab.local" in n


def test_metadata_embeds_encoded_network():
    m = build_metadata(
        "web1",
        domain="example.com",
        ip="10.0.1.5",
        prefix=24,
        gateway="10.0.1.1",
        dns="8.8.8.8",
    )
    assert "instance-id: web1" in m
    assert "network.encoding: base64" in m
    # network: <b64>
    line = [ln for ln in m.splitlines() if ln.startswith("network: ")][0]
    b64 = line.split(" ", 1)[1]
    raw = base64.b64decode(b64).decode()
    assert "10.0.1.5/24" in raw
    assert "via: 10.0.1.1" in raw


def test_userdata_has_hostname_not_network():
    u = build_userdata("web1", "10.0.1.5", 24, gateway="10.0.1.1", dns="10.0.1.2")
    assert "hostname: web1" in u
    assert "network:" not in u
    assert "10.0.1.5" not in u


def test_guestinfo_is_base64_and_has_network_keys():
    g = guestinfo_extra_config(
        "n1", "192.168.1.10", 19, gateway="192.168.1.1", dns="1.1.1.1"
    )
    assert g["guestinfo.metadata.encoding"] == "base64"
    assert g["guestinfo.userdata.encoding"] == "base64"
    assert g["guestinfo.networkconfig.encoding"] == "base64"

    meta = base64.b64decode(g["guestinfo.metadata"]).decode()
    assert "network.encoding: base64" in meta
    net_line = [ln for ln in meta.splitlines() if ln.startswith("network: ")][0]
    net = base64.b64decode(net_line.split(" ", 1)[1]).decode()
    assert "192.168.1.10/19" in net

    user = base64.b64decode(g["guestinfo.userdata"]).decode()
    assert "network:" not in user

    net2 = base64.b64decode(g["guestinfo.networkconfig"]).decode()
    assert "192.168.1.10/19" in net2
