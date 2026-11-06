"""vsphere.connect timeout plumbing (no live vCenter)."""

from __future__ import annotations

from unittest.mock import patch

from ovbuilder import vsphere


def test_connect_passes_timeout_to_smartconnect():
    with patch.object(vsphere, "SmartConnect", return_value="si") as smart:
        assert vsphere.connect("vc.example.com", "u", "p", timeout=15) == "si"
    assert smart.call_args.kwargs["httpConnectionTimeout"] == 15


def test_connect_without_timeout_keeps_pyvmomi_default():
    with patch.object(vsphere, "SmartConnect", return_value="si") as smart:
        vsphere.connect("vc.example.com", "u", "p")
    assert "httpConnectionTimeout" not in smart.call_args.kwargs
