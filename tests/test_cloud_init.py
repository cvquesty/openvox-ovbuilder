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


def test_userdata_without_password_omits_chpasswd():
    u = build_userdata(
        "web1", "10.0.1.5", 24, gateway="10.0.1.1", password=None
    )
    assert "config: disabled" in u
    assert "chpasswd:" not in u
    assert "ovbuilder-net.sh" in u


def test_userdata_with_password_includes_chpasswd():
    u = build_userdata(
        "web1",
        "10.0.1.5",
        24,
        default_user="ubuntu",
        password="unit-test-only-password",
    )
    assert "ubuntu:unit-test-only-password" in u
    assert "root:unit-test-only-password" in u


def test_guestinfo_uses_env_password(monkeypatch=None):
    # stdlib-friendly: set env without pytest monkeypatch
    key = "OVBUILDER_GOLDEN_PASSWORD"
    old = os.environ.get(key)
    os.environ[key] = "env-lab-password-not-for-prod"
    try:
        g = guestinfo_extra_config(
            "n1", "192.168.1.10", 19, gateway="192.168.1.1", default_user="ubuntu"
        )
        user = base64.b64decode(g["guestinfo.userdata"]).decode()
        assert "env-lab-password-not-for-prod" in user
        # Password content comes only from env/secrets, never a module default.
        assert "GOLDEN_DEFAULT_PASSWORD" not in user
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
