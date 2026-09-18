"""Non-interactive CLI placement flags (no vCenter)."""

from __future__ import annotations

from unittest.mock import patch

from ovbuilder.build import _resolve_golden_and_compute, apply_cli_placement
from ovbuilder.config import OvbuilderConfig
from ovbuilder.goldens import GoldenTemplate
from placeholders import placeholder_value


def test_apply_cli_placement_overrides_config():
    cfg = OvbuilderConfig()
    apply_cli_placement(
        cfg,
        cluster="Lab Cluster",
        networks=["VM Dev", "VM Extra"],
        vm_datastore_cluster="YAVIN-DEV",
        datacenter="Main DC",
    )
    assert cfg.cluster == "Lab Cluster"
    assert cfg.networks == ["VM Dev", "VM Extra"]
    assert cfg.vm_datastore_cluster == "YAVIN-DEV"
    assert cfg.datacenter == "Main DC"


def test_apply_cli_placement_splits_comma_networks():
    cfg = OvbuilderConfig()
    apply_cli_placement(cfg, networks=["VM Production, VM Development"])
    assert cfg.networks == ["VM Production", "VM Development"]


def test_apply_cli_placement_ignores_empty():
    cfg = OvbuilderConfig(cluster="Keep", networks=["KeepNet"])
    apply_cli_placement(cfg, cluster=None, networks=None)
    assert cfg.cluster == "Keep"
    assert cfg.networks == ["KeepNet"]


def test_resolve_golden_without_vcenter_uses_config():
    cfg = OvbuilderConfig()
    name, guest_id, user, dc, compute = _resolve_golden_and_compute(
        cfg,
        vsphere_server=None,
        vsphere_user=None,
        vsphere_password=None,
        os_image="ubuntu-24.04",
        template_name="",
        guest_id="",
        default_user="root",
        template_datacenter="",
        compute_type="cluster",
        cluster_name="esx1",
        datacenter="SEA3 - Bellevue",
    )
    assert name == "ovbuilder-ubuntu-24.04"
    assert user == "ubuntu"
    assert dc == ""
    assert compute == "cluster"


def test_resolve_golden_live_sets_source_dc_and_host_type():
    cfg = OvbuilderConfig()
    live = [
        GoldenTemplate(
            name="ovbuilder-ubuntu-24.04",
            datacenter="PDXC",
            key="ubuntu-24.04",
            label="Ubuntu 24.04 LTS",
            default_user="ubuntu",
            guest_id="ubuntu64Guest",
        )
    ]
    creds = placeholder_value()
    with patch("ovbuilder.build.vsphere.connect", return_value=object()), patch(
        "ovbuilder.build.vsphere.disconnect"
    ), patch("ovbuilder.build.discover_goldens", return_value=live), patch(
        "ovbuilder.build.vsphere.classify_compute", return_value="host"
    ):
        name, guest_id, user, dc, compute = _resolve_golden_and_compute(
            cfg,
            vsphere_server="vc.example.com",
            vsphere_user="operator",
            vsphere_password=creds,
            os_image="ubuntu-24.04",
            template_name="",
            guest_id="",
            default_user="root",
            template_datacenter="",
            compute_type="cluster",
            cluster_name="esx1.sea3.office.example.net",
            datacenter="SEA3 - Bellevue",
        )
    assert name == "ovbuilder-ubuntu-24.04"
    assert guest_id == "ubuntu64Guest"
    assert user == "ubuntu"
    assert dc == "PDXC"
    assert compute == "host"
