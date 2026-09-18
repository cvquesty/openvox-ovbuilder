"""Cluster listing and pre-Terraform template checks (no live vCenter)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ovbuilder import vsphere


def _entity(wsdl: str, name: str, **attrs):
    item = MagicMock()
    item._wsdlName = wsdl
    item.name = name
    for key, value in attrs.items():
        setattr(item, key, value)
    return item


def _dc_with(*children):
    dc = MagicMock()
    dc.hostFolder.childEntity = list(children)
    return dc


def test_list_clusters_filters_standalone_hosts_and_folders():
    cluster = _entity("ClusterComputeResource", "ATLC Cluster")
    host_cr = _entity(
        "ComputeResource",
        "esx1.sea3.office.example.net",
        host=[_entity("HostSystem", "esx1.sea3.office.example.net")],
    )
    nested_host = _entity(
        "ComputeResource",
        "esx2.sea3.office.example.net",
        host=[_entity("HostSystem", "esx2.sea3.office.example.net")],
    )
    folder = _entity("Folder", "Hosts", childEntity=[nested_host])
    dc = _dc_with(cluster, host_cr, folder)

    with patch.object(vsphere, "_find_datacenter", return_value=dc):
        assert vsphere.list_clusters(object(), "SEA3 - Bellevue") == ["ATLC Cluster"]
        assert vsphere.list_standalone_hosts(object(), "SEA3 - Bellevue") == [
            "esx1.sea3.office.example.net",
            "esx2.sea3.office.example.net",
        ]


def test_list_clusters_empty_when_only_standalone_hosts():
    host_cr = _entity(
        "ComputeResource",
        "esx1.sea3.office.example.net",
        host=[_entity("HostSystem", "esx1.sea3.office.example.net")],
    )
    with patch.object(vsphere, "_find_datacenter", return_value=_dc_with(host_cr)):
        assert vsphere.list_clusters(object(), "SEA3 - Bellevue") == []
        assert vsphere.list_standalone_hosts(object(), "SEA3 - Bellevue") == [
            "esx1.sea3.office.example.net"
        ]


def test_classify_compute_cluster_vs_host():
    cluster = _entity("ClusterComputeResource", "Production Cluster")
    host_cr = _entity(
        "ComputeResource",
        "esx1.sea3.office.example.net",
        host=[_entity("HostSystem", "esx1.sea3.office.example.net")],
    )
    with patch.object(vsphere, "_find_datacenter", return_value=_dc_with(cluster, host_cr)):
        assert vsphere.classify_compute(object(), "ATLC", "Production Cluster") == "cluster"
        assert (
            vsphere.classify_compute(object(), "SEA3 - Bellevue", "esx1.sea3.office.example.net")
            == "host"
        )


def test_looks_like_host_fqdn():
    assert vsphere.looks_like_host_fqdn("esx1.sea3.office.example.net")
    assert vsphere.looks_like_host_fqdn("esx2.sea3.office.twttr.net")
    assert not vsphere.looks_like_host_fqdn("Production Cluster")
    assert not vsphere.looks_like_host_fqdn("esx1")
    assert not vsphere.looks_like_host_fqdn("")


def test_classify_compute_fail_open_does_not_default_fqdn_to_cluster():
    with patch.object(
        vsphere, "list_clusters", side_effect=RuntimeError("vCenter timeout")
    ):
        with pytest.raises(RuntimeError, match="looks like an ESXi host FQDN"):
            vsphere.classify_compute(
                object(), "SEA3 - Bellevue", "esx1.sea3.office.example.net"
            )


def test_require_template_missing_in_every_dc_fails_early():
    with patch.object(
        vsphere, "template_exists_in_datacenter", return_value=False
    ), patch.object(vsphere, "find_template_datacenters", return_value=[]):
        with pytest.raises(RuntimeError, match="not found in any vSphere") as excinfo:
            vsphere.require_template(
                object(), "ovbuilder-ubuntu-24.04", "SEA3 - Bellevue"
            )
    assert "ovbuilder-ubuntu-24.04" in str(excinfo.value)
    assert "Mark as Template" in str(excinfo.value)


def test_require_template_found_in_other_dc_returns_that_dc():
    with patch.object(
        vsphere, "template_exists_in_datacenter", return_value=False
    ), patch.object(vsphere, "find_template_datacenters", return_value=["PDXC"]):
        assert (
            vsphere.require_template(
                object(), "ovbuilder-ubuntu-24.04", "SEA3 - Bellevue"
            )
            == "PDXC"
        )


def test_require_template_prefers_selected_dc_when_present():
    with patch.object(vsphere, "template_exists_in_datacenter", return_value=True):
        assert vsphere.require_template(object(), "ovbuilder-ubuntu-24.04", "PDXC") == "PDXC"


def test_template_exists_skips_powered_on_vm_with_same_name():
    clone = MagicMock()
    clone.name = "ovbuilder-ubuntu-24.04"
    clone.config.template = False
    view = MagicMock()
    view.view = [clone]
    content = MagicMock()
    content.viewManager.CreateContainerView.return_value = view

    with patch.object(vsphere, "_find_datacenter", return_value=MagicMock()), patch.object(
        vsphere, "_content", return_value=content
    ):
        assert (
            vsphere.template_exists_in_datacenter(
                object(), "ovbuilder-ubuntu-24.04", "SEA3 - Bellevue"
            )
            is False
        )
