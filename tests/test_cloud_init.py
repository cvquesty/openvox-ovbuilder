"""Unit tests for cloud-init guestinfo helpers."""

from __future__ import annotations

import base64
import os

from ovbuilder.cloud_init import (
    build_metadata,
    build_userdata,
    guestinfo_extra_config,
    normalize_ssh_authorized_keys,
    _network_configure_script,
)
from ovbuilder.openvox_site import DEFAULT_SITES

from placeholders import placeholder_value

# Well-formed public key for tests (not a credential).
_TEST_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    " test@ovbuilder"
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


def test_userdata_defaults_are_hardened():
    u = build_userdata("web1", "10.0.1.5", 24, gateway="10.0.1.1")
    assert "ssh_pwauth: false" in u
    assert "disable_root: true" in u
    assert "ssh_pwauth: true" not in u
    assert "disable_root: false" not in u
    assert "chpasswd:" not in u
    assert "echo 'root:" not in u
    assert "ovbuilder-net.sh" in u
    assert "ovbuilder-dnf-groups.sh" not in u


def test_userdata_without_password_omits_chpasswd():
    u = build_userdata(
        "web1", "10.0.1.5", 24, gateway="10.0.1.1", password=None
    )
    assert "config: disabled" in u
    assert "chpasswd:" not in u
    assert "ovbuilder-net.sh" in u
    # No groups passed → no DNF script.
    assert "ovbuilder-dnf-groups.sh" not in u


def test_userdata_password_without_opt_in_is_ignored():
    guest = placeholder_value()
    u = build_userdata(
        "web1",
        "10.0.1.5",
        24,
        default_user="ubuntu",
        password=guest,
    )
    assert "ssh_pwauth: false" in u
    assert "disable_root: true" in u
    assert "chpasswd:" not in u
    assert f"ubuntu:{guest}" not in u
    assert f"root:{guest}" not in u
    assert guest not in u


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


def test_userdata_opt_in_password_ssh_user_only():
    guest = placeholder_value()
    u = build_userdata(
        "web1",
        "10.0.1.5",
        24,
        default_user="ubuntu",
        password=guest,
        allow_password_ssh=True,
    )
    assert "ssh_pwauth: true" in u
    assert "disable_root: true" in u
    assert f"ubuntu:{guest}" in u
    assert f"root:{guest}" not in u
    assert "echo 'root:" not in u
    assert "GOLDEN_DEFAULT_PASSWORD" not in u


def test_userdata_embeds_authorized_keys():
    u = build_userdata(
        "web1",
        "10.0.1.5",
        24,
        ssh_authorized_keys=[_TEST_PUBKEY],
    )
    assert "ssh_pwauth: false" in u
    assert "disable_root: true" in u
    assert "ssh_authorized_keys:" in u
    assert _TEST_PUBKEY in u
    assert "chpasswd:" not in u


def test_normalize_ssh_authorized_keys_drops_private_material():
    blob = "\n".join(
        [
            _TEST_PUBKEY,
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "AAAA",
            "-----END OPENSSH PRIVATE KEY-----",
            "not-a-key",
            _TEST_PUBKEY,
        ]
    )
    keys = normalize_ssh_authorized_keys(blob)
    assert keys == [_TEST_PUBKEY]


def test_guestinfo_default_does_not_inject_env_password():
    key = "OVBUILDER_GOLDEN_PASSWORD"
    old = os.environ.get(key)
    old_allow = os.environ.get("OVBUILDER_ALLOW_PASSWORD_SSH")
    guest = placeholder_value()
    os.environ[key] = guest
    os.environ.pop("OVBUILDER_ALLOW_PASSWORD_SSH", None)
    try:
        g = guestinfo_extra_config(
            "n1",
            "192.168.1.10",
            19,
            gateway="192.168.1.1",
            default_user="ubuntu",
            ssh_authorized_keys=[],
        )
        user = base64.b64decode(g["guestinfo.userdata"]).decode()
        meta = base64.b64decode(g["guestinfo.metadata"]).decode()
        assert "ssh_pwauth: false" in user
        assert "disable_root: true" in user
        assert "chpasswd:" not in user
        assert guest not in user
        assert f"root:{guest}" not in user
        assert "instance-id: n1" in meta
        assert "GOLDEN_DEFAULT_PASSWORD" not in user
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
        if old_allow is None:
            os.environ.pop("OVBUILDER_ALLOW_PASSWORD_SSH", None)
        else:
            os.environ["OVBUILDER_ALLOW_PASSWORD_SSH"] = old_allow


def test_guestinfo_works_without_golden_password(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("OVBUILDER_GOLDEN_PASSWORD", raising=False)
    monkeypatch.delenv("OVBUILDER_ALLOW_PASSWORD_SSH", raising=False)
    monkeypatch.delenv("OVBUILDER_SSH_AUTHORIZED_KEYS", raising=False)
    monkeypatch.delenv("OVBUILDER_SSH_AUTHORIZED_KEYS_FILE", raising=False)
    g = guestinfo_extra_config(
        "n1",
        "192.168.1.10",
        19,
        gateway="192.168.1.1",
        default_user="ubuntu",
        ssh_authorized_keys=[],
    )
    user = base64.b64decode(g["guestinfo.userdata"]).decode()
    assert "ssh_pwauth: false" in user
    assert "disable_root: true" in user
    assert "chpasswd:" not in user


def test_guestinfo_injects_env_authorized_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("OVBUILDER_SSH_AUTHORIZED_KEYS", _TEST_PUBKEY)
    monkeypatch.delenv("OVBUILDER_ALLOW_PASSWORD_SSH", raising=False)
    monkeypatch.delenv("OVBUILDER_GOLDEN_PASSWORD", raising=False)
    monkeypatch.delenv("OVBUILDER_SSH_AUTHORIZED_KEYS_FILE", raising=False)
    g = guestinfo_extra_config(
        "n1", "192.168.1.10", 19, gateway="192.168.1.1", default_user="ubuntu"
    )
    user = base64.b64decode(g["guestinfo.userdata"]).decode()
    assert "ssh_authorized_keys:" in user
    assert _TEST_PUBKEY in user
    assert "ssh_pwauth: false" in user
    assert "chpasswd:" not in user


def test_guestinfo_opt_in_uses_env_password_not_root():
    key = "OVBUILDER_GOLDEN_PASSWORD"
    old = os.environ.get(key)
    guest = placeholder_value()
    os.environ[key] = guest
    try:
        g = guestinfo_extra_config(
            "n1",
            "192.168.1.10",
            19,
            gateway="192.168.1.1",
            default_user="ubuntu",
            allow_password_ssh=True,
            ssh_authorized_keys=[],
        )
        user = base64.b64decode(g["guestinfo.userdata"]).decode()
        assert guest in user
        assert f"ubuntu:{guest}" in user
        assert f"root:{guest}" not in user
        assert "ssh_pwauth: true" in user
        assert "disable_root: true" in user
        assert "GOLDEN_DEFAULT_PASSWORD" not in user
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
