"""Celery worker command uses the shared environment mapping."""

from __future__ import annotations

from unittest.mock import patch

from app.tasks import _build_command


def test_build_command_uses_placement_mapping():
    with patch("app.tasks.datastore_cluster_for", return_value="HOTH_PROD"):
        cmd, _env = _build_command(
            {
                "hostname": "web-01",
                "ip": "10.0.0.10",
                "os_image": "ubuntu-24.04",
                "environment": "prod",
                "cluster": "Prod Cluster",
                "network": "VM Production",
            },
            "job-1",
        )
    assert "--vm-datastore-cluster" in cmd
    assert cmd[cmd.index("--vm-datastore-cluster") + 1] == "HOTH_PROD"
    assert cmd[cmd.index("--cluster") + 1] == "Prod Cluster"
    assert cmd[cmd.index("--network") + 1] == "VM Production"
    assert "--yes" in cmd


def test_build_command_omits_empty_datastore_cluster():
    with patch("app.tasks.datastore_cluster_for", return_value=""):
        cmd, _env = _build_command(
            {
                "hostname": "web-01",
                "ip": "10.0.0.10",
                "os_image": "ubuntu-24.04",
                "environment": "dev",
            },
            "job-2",
        )
    assert "--vm-datastore-cluster" not in cmd
    assert "--cluster" not in cmd
