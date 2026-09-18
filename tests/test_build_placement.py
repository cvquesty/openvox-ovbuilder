"""Non-interactive CLI placement flags (no vCenter)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import typer

from ovbuilder.build import (
    _prompt_compute_placement,
    _resolve_golden_and_compute,
    _verify_clone_template,
    apply_cli_placement,
)
from ovbuilder.config import OvbuilderConfig
from ovbuilder.goldens import COMPUTE_TYPE_HOST, GoldenTemplate
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
    ), patch(
        "ovbuilder.build.vsphere.require_template"
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


def test_resolve_golden_missing_template_fails_early():
    cfg = OvbuilderConfig()
    creds = placeholder_value()
    with patch("ovbuilder.build.vsphere.connect", return_value=object()), patch(
        "ovbuilder.build.vsphere.disconnect"
    ), patch("ovbuilder.build.discover_goldens", return_value=[]), patch(
        "ovbuilder.build.vsphere.classify_compute", return_value="cluster"
    ), patch(
        "ovbuilder.build.vsphere.require_template",
        side_effect=RuntimeError(
            "Packer template 'ovbuilder-ubuntu-24.04' was not found in "
            "datacenter 'SEA3 - Bellevue'"
        ),
    ):
        with pytest.raises(typer.Exit):
            _resolve_golden_and_compute(
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
                cluster_name="Production Cluster",
                datacenter="SEA3 - Bellevue",
            )


def test_verify_clone_template_exits_when_missing():
    with patch(
        "ovbuilder.build.vsphere.require_template",
        side_effect=RuntimeError(
            "Packer template 'ovbuilder-ubuntu-24.04' was not found in "
            "datacenter 'SEA3 - Bellevue'"
        ),
    ):
        with pytest.raises(typer.Exit):
            _verify_clone_template(
                object(),
                template_name="ovbuilder-ubuntu-24.04",
                template_datacenter="",
                placement_datacenter="SEA3 - Bellevue",
            )


def test_prompt_compute_uses_clusters_only_when_present():
    with patch(
        "ovbuilder.build.vsphere.list_clusters",
        return_value=["Production Cluster", "Lab Cluster"],
    ), patch(
        "ovbuilder.build.vsphere.list_standalone_hosts",
        return_value=["esx1.sea3.office.example.net"],
    ), patch(
        "ovbuilder.build._prompt_table_choice", return_value="Production Cluster"
    ) as prompt:
        name, kind = _prompt_compute_placement(object(), "ATLC")
    assert name == "Production Cluster"
    assert kind == "cluster"
    prompt.assert_called_once()
    assert prompt.call_args[0][0] == "Clusters in ATLC"
    assert prompt.call_args[0][1] == ["Production Cluster", "Lab Cluster"]


def test_prompt_compute_hosts_only_when_no_cluster():
    with patch("ovbuilder.build.vsphere.list_clusters", return_value=[]), patch(
        "ovbuilder.build.vsphere.list_standalone_hosts",
        return_value=["esx1.sea3.office.example.net", "esx2.sea3.office.example.net"],
    ), patch(
        "ovbuilder.build._prompt_table_choice",
        return_value="esx1.sea3.office.example.net",
    ) as prompt:
        name, kind = _prompt_compute_placement(object(), "SEA3 - Bellevue")
    assert name == "esx1.sea3.office.example.net"
    assert kind == COMPUTE_TYPE_HOST
    assert "Standalone ESXi hosts" in prompt.call_args[0][0]


def test_prompt_compute_refuses_when_no_cluster_or_host():
    with patch("ovbuilder.build.vsphere.list_clusters", return_value=[]), patch(
        "ovbuilder.build.vsphere.list_standalone_hosts", return_value=[]
    ):
        with pytest.raises(typer.Exit):
            _prompt_compute_placement(object(), "SEA3 - Bellevue")
