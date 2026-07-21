"""Unit tests for cloud-init guestinfo helpers."""

from __future__ import annotations

import base64

from ovbuilder.cloud_init import build_userdata, guestinfo_extra_config


def test_userdata_contains_static_address():
    u = build_userdata("web1", "10.0.1.5", 24, gateway="10.0.1.1", dns="10.0.1.2")
    assert "10.0.1.5/24" in u
    assert "via: 10.0.1.1" in u
    assert "hostname: web1" in u
    assert 'name: "e*"' in u


def test_guestinfo_is_base64():
    g = guestinfo_extra_config("n1", "192.168.1.10", 19)
    assert g["guestinfo.metadata.encoding"] == "base64"
    assert g["guestinfo.userdata.encoding"] == "base64"
    raw = base64.b64decode(g["guestinfo.userdata"]).decode()
    assert "192.168.1.10/19" in raw
