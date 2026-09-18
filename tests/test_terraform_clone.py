"""Terraform clone payload + HCL wiring for cross-DC / standalone host."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ovbuilder.config import OvbuilderConfig
from ovbuilder.terraform import run_terraform_apply

ROOT = Path(__file__).resolve().parents[1]
VM_MAIN = (ROOT / "terraform" / "modules" / "vm" / "main.tf").read_text(encoding="utf-8")
VM_VARS = (ROOT / "terraform" / "modules" / "vm" / "variables.tf").read_text(
    encoding="utf-8"
)
ROOT_MAIN = (ROOT / "terraform" / "main.tf").read_text(encoding="utf-8")
ROOT_PROVIDERS = (ROOT / "terraform" / "providers.tf").read_text(encoding="utf-8")


def test_module_looks_up_template_in_source_datacenter():
    assert "template_datacenter" in VM_VARS
    assert "vsphere_datacenter" in VM_MAIN
    assert "template_dc" in VM_MAIN
    assert "data.vsphere_datacenter.template_dc" in VM_MAIN
    assert "data.vsphere_datacenter.dc.id" in VM_MAIN


def test_module_places_standalone_host_via_vsphere_host():
    assert "compute_type" in VM_VARS
    assert 'data "vsphere_host" "host"' in VM_MAIN
    assert "resource_pool_id" in VM_MAIN
    assert "data.vsphere_host.host[0].resource_pool_id" in VM_MAIN
    assert "data.vsphere_compute_cluster.cluster[0].resource_pool_id" in VM_MAIN


def test_root_does_not_force_compute_cluster_lookup():
    assert 'data "vsphere_compute_cluster"' not in ROOT_PROVIDERS
    assert "template_datacenter" in ROOT_MAIN
    assert "compute_type" in ROOT_MAIN


def test_apply_writes_template_datacenter_and_host_compute_type(tmp_path):
    tf_dir = tmp_path / "terraform"
    tf_dir.mkdir()
    (tf_dir / "main.tf").write_text("# stub\n", encoding="utf-8")
    stated = tmp_path / "state"
    stated.mkdir()
    cfg = OvbuilderConfig()
    captured = {}

    def _fake_write(path, payload):
        captured["payload"] = payload
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    with patch("ovbuilder.terraform.ensure_terraform_init", return_value=True), patch(
        "ovbuilder.terraform.migrate_legacy_shared_state"
    ), patch("ovbuilder.terraform._write_var_file", side_effect=_fake_write), patch(
        "ovbuilder.terraform.subprocess.run"
    ), patch(
        "ovbuilder.terraform.state_dir_for_vm", return_value=stated
    ), patch(
        "ovbuilder.terraform.state_file_for_vm",
        return_value=stated / "terraform.tfstate",
    ):
        ok = run_terraform_apply(
            tf_dir,
            {
                "vm_name": "web-01",
                "template_name": "ovbuilder-ubuntu-24.04",
                "template_datacenter": "PDXC",
                "compute_type": "host",
                "provision_mode": "clone",
                "datacenter": "SEA3 - Bellevue",
                "cluster": "esx1.sea3.office.example.net",
            },
            cfg,
        )
    assert ok is True
    payload = captured["payload"]
    assert payload["template_name"] == "ovbuilder-ubuntu-24.04"
    assert payload["template_datacenter"] == "PDXC"
    assert payload["compute_type"] == "host"
    assert payload["vsphere_datacenter"] == "SEA3 - Bellevue"
    assert payload["vsphere_cluster"] == "esx1.sea3.office.example.net"
