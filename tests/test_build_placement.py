"""Non-interactive CLI placement flags (no vCenter)."""

from __future__ import annotations

from ovbuilder.build import apply_cli_placement
from ovbuilder.config import OvbuilderConfig


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
