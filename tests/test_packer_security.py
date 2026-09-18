"""Defensive checks for Packer golden seeds and install scripts.

These assertions lock the High-severity insecure-default findings:
no baked-in well-known password, no NOPASSWD, root locked, Alma firewall
and SELinux enforcing. They do not describe or reproduce an attack.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKER = ROOT / "packer"

UBUNTU_USERDATA = PACKER / "ubuntu-24.04" / "http" / "user-data.pkrtpl"
ALMA_KS = PACKER / "almalinux-10" / "http" / "ks.cfg.pkrtpl"
INSTALL_UBUNTU = PACKER / "scripts" / "install-ubuntu.sh"
INSTALL_ALMA = PACKER / "scripts" / "install-alma.sh"


def test_ubuntu_autoinstall_uses_operator_password_only():
    text = UBUNTU_USERDATA.read_text(encoding="utf-8")
    assert "ubuntu:ubuntu" not in text
    assert 'password: "${ssh_password_crypted}"' in text
    assert "ubuntu:${ssh_password}" in text
    assert "disable_root: true" in text
    assert "disable_root: false" not in text
    assert "passwd -l root" in text
    assert "NOPASSWD:ALL" not in text
    assert "sudoers.d/ubuntu" not in text
    assert "passwd -u root" not in text
    assert "chage -d 0" not in text


def test_alma_kickstart_hardens_firewall_selinux_and_sudo():
    text = ALMA_KS.read_text(encoding="utf-8")
    assert "firewall --disabled" not in text
    assert "selinux --permissive" not in text
    assert "firewall --enabled --ssh" in text
    assert "selinux --enforcing" in text
    assert "rootpw --lock" in text
    assert "--password=${ssh_password}" in text
    assert "NOPASSWD:ALL" not in text
    assert "%wheel ALL=(ALL) ALL" in text


def test_install_scripts_do_not_grant_nopasswd():
    ubuntu = INSTALL_UBUNTU.read_text(encoding="utf-8")
    alma = INSTALL_ALMA.read_text(encoding="utf-8")
    assert "NOPASSWD:ALL" not in ubuntu
    assert "NOPASSWD:ALL" not in alma
    assert "rm -f /etc/sudoers.d/ubuntu" in ubuntu
    assert "rm -f /etc/sudoers.d/almalinux /etc/sudoers.d/cloud-user" in alma
    assert "%wheel ALL=(ALL) ALL" in alma
