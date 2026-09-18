"""Celery worker command uses the shared environment mapping."""

from __future__ import annotations

from unittest.mock import patch

from app.tasks import (
    _LOG_PERSIST_INTERVAL,
    _STATUS_POLL_INTERVAL,
    _build_command,
    _should_flush_logs,
    _should_poll_status,
    run_build,
)


def test_build_command_uses_placement_mapping():
    with patch("app.tasks.datastore_cluster_for", return_value="HOTH_PROD"), patch(
        "app.tasks.configured_datacenter", return_value=""
    ):
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
    with patch("app.tasks.datastore_cluster_for", return_value=""), patch(
        "app.tasks.configured_datacenter", return_value=""
    ):
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
    assert "--datacenter" not in cmd


def test_build_command_passes_inventory_datacenter():
    with patch("app.tasks.datastore_cluster_for", return_value=""), patch(
        "app.tasks.configured_datacenter", return_value="SEA3 - Bellevue"
    ):
        cmd, _env = _build_command(
            {
                "hostname": "web-01",
                "ip": "10.0.0.10",
                "os_image": "ubuntu-24.04",
                "environment": "dev",
                "cluster": "esx1.sea3.office.example.net",
            },
            "job-3",
        )
    assert cmd[cmd.index("--datacenter") + 1] == "SEA3 - Bellevue"
    assert cmd[cmd.index("--cluster") + 1] == "esx1.sea3.office.example.net"
    assert cmd[cmd.index("--os") + 1] == "ubuntu-24.04"


def test_status_poll_is_not_per_line():
    assert not _should_poll_status(1, 0.1)
    assert _should_poll_status(_STATUS_POLL_INTERVAL, 0.1)
    assert _should_poll_status(1, 2.0)


def test_log_flush_on_interval_time_or_status_change():
    assert not _should_flush_logs(1, 0.1)
    assert _should_flush_logs(_LOG_PERSIST_INTERVAL, 0.1)
    assert _should_flush_logs(1, 2.0)
    assert _should_flush_logs(1, 0.1, status_changed=True)


def test_run_build_ignores_celery_result():
    assert run_build.ignore_result is True
