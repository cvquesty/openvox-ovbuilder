"""Environment → datastore-cluster mapping (no vCenter required)."""

from __future__ import annotations

from pathlib import Path

from ovbuilder.config import ConfigManager, EnvironmentProfile
from ovbuilder.placement import (
    datastore_cluster_for,
    default_environments,
    environments_as_dicts,
    os_images_as_dicts,
    resolved_environments,
)


def test_defaults_include_dev_and_prod():
    envs = default_environments()
    assert "dev" in envs and "prod" in envs
    assert envs["dev"].datastore_cluster == "YAVIN-DEV"
    assert envs["prod"].datastore_cluster == "YAVIN-PROD"


def test_config_yaml_is_source_of_truth(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OVBUILDER_ENV_DEV_DATASTORE_CLUSTER", raising=False)
    (tmp_path / "config.yaml").write_text(
        "environments:\n"
        "  lab:\n"
        "    label: Lab\n"
        "    datastore_cluster: HOTH_DEV\n"
        "    cluster: Lab Cluster\n",
        encoding="utf-8",
    )
    cfg = ConfigManager(config_dir=tmp_path).load_config()
    rows = environments_as_dicts(cfg)
    assert len(rows) == 1
    assert rows[0]["key"] == "lab"
    assert rows[0]["datastore_cluster"] == "HOTH_DEV"
    assert rows[0]["cluster"] == "Lab Cluster"
    assert datastore_cluster_for("lab", cfg) == "HOTH_DEV"


def test_env_var_overlays_datastore_cluster(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OVBUILDER_ENV_DEV_DATASTORE_CLUSTER", "CUSTOM-DEV")
    monkeypatch.setenv("OVBUILDER_ENV_DEV_LABEL", "Custom Dev")
    cfg = ConfigManager(config_dir=tmp_path).load_config()
    resolved = resolved_environments(cfg)
    assert resolved["dev"].datastore_cluster == "CUSTOM-DEV"
    assert resolved["dev"].label == "Custom Dev"
    assert datastore_cluster_for("dev", cfg) == "CUSTOM-DEV"


def test_unknown_environment_falls_back_to_dev():
    assert datastore_cluster_for("nope") == "YAVIN-DEV"


def test_os_images_from_config_defaults():
    rows = os_images_as_dicts()
    keys = {r["key"] for r in rows}
    assert "ubuntu-24.04" in keys
    assert "almalinux-10" in keys


def test_shorthand_yaml_string_is_datastore_cluster(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "environments:\n  staging: STAGING-SDRS\n",
        encoding="utf-8",
    )
    cfg = ConfigManager(config_dir=tmp_path).load_config()
    assert cfg.environments["staging"].datastore_cluster == "STAGING-SDRS"
    assert isinstance(cfg.environments["staging"], EnvironmentProfile)


def test_env_var_does_not_treat_datastore_cluster_as_cluster(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OVBUILDER_ENV_DEV_DATASTORE_CLUSTER", "ONLY-DS")
    cfg = ConfigManager(config_dir=tmp_path).load_config()
    resolved = resolved_environments(cfg)
    assert resolved["dev"].datastore_cluster == "ONLY-DS"
    assert resolved["dev"].cluster == ""
