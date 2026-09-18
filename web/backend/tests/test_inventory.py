"""Inventory API tests — mocked vSphere client, no live vCenter."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import inventory as inventory_api
from app.auth import get_current_user
from app.models import Role, UserOut
from ovbuilder.goldens import GoldenTemplate


def _app_for(role: Role) -> FastAPI:
    app = FastAPI()
    app.include_router(inventory_api.router, prefix="/api/inventory")

    async def _user() -> UserOut:
        return UserOut(username=role.value, role=role)

    app.dependency_overrides[get_current_user] = _user
    return app


def _client(role: Role = Role.builder) -> TestClient:
    return TestClient(_app_for(role))


@contextmanager
def _session(datacenter: str = "Main DC"):
    yield (object(), datacenter)


def test_environments_come_from_shared_mapping():
    res = _client().get("/api/inventory/environments")
    assert res.status_code == 200
    rows = res.json()
    keys = {row["key"] for row in rows}
    assert "dev" in keys and "prod" in keys
    by_key = {row["key"]: row for row in rows}
    assert by_key["dev"]["datastore_cluster"]
    assert by_key["prod"]["datastore_cluster"]


from ovbuilder.goldens import GoldenTemplate


def test_os_images_from_config():
    res = _client().get("/api/inventory/os-images")
    assert res.status_code == 200
    keys = {row["key"] for row in res.json()}
    assert "ubuntu-24.04" in keys


def test_os_images_live_ovbuilder_only_omits_source_dc():
    live = [
        GoldenTemplate(
            name="ovbuilder-ubuntu-24.04",
            datacenter="PDXC",
            datastore="HOTH_DEV",
            key="ubuntu-24.04",
            label="Ubuntu 24.04 LTS",
            default_user="ubuntu",
        )
    ]

    with patch.object(inventory_api, "vsphere_session", _session), patch.object(
        inventory_api, "discover_goldens", return_value=live
    ):
        res = _client().get("/api/inventory/os-images")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "ovbuilder-ubuntu-24.04"
    assert rows[0]["label"] == "Ubuntu 24.04 LTS"
    assert "datacenter" not in rows[0]
    assert "datastore" not in rows[0]


def test_os_images_live_empty_does_not_invent_config_names():
    with patch.object(inventory_api, "vsphere_session", _session), patch.object(
        inventory_api, "discover_goldens", return_value=[]
    ):
        res = _client().get("/api/inventory/os-images")
    assert res.status_code == 200
    assert res.json() == []


def test_clusters_live_success():
    fake_vsphere = MagicMock()
    fake_vsphere.list_clusters.return_value = ["Production Cluster", "Lab Cluster"]
    with patch.object(inventory_api, "vsphere_session", _session), patch.object(
        inventory_api, "vsphere", fake_vsphere
    ):
        res = _client().get("/api/inventory/clusters")
    assert res.status_code == 200
    assert res.json() == ["Production Cluster", "Lab Cluster"]
    fake_vsphere.list_clusters.assert_called_once()


def test_networks_live_success():
    fake_vsphere = MagicMock()
    fake_vsphere.list_networks.return_value = ["VM Production"]
    with patch.object(inventory_api, "vsphere_session", _session), patch.object(
        inventory_api, "vsphere", fake_vsphere
    ):
        res = _client().get("/api/inventory/networks")
    assert res.status_code == 200
    assert res.json() == ["VM Production"]


def test_datacenters_live_success():
    fake_vsphere = MagicMock()
    fake_vsphere.list_datacenters.return_value = ["Main DC"]
    with patch.object(inventory_api, "vsphere_session", _session), patch.object(
        inventory_api, "vsphere", fake_vsphere
    ):
        res = _client().get("/api/inventory/datacenters")
    assert res.status_code == 200
    assert res.json() == ["Main DC"]


def test_datastore_clusters_live_success():
    fake_vsphere = MagicMock()
    fake_vsphere.list_datastore_clusters.return_value = ["YAVIN-DEV", "YAVIN-PROD"]
    with patch.object(inventory_api, "vsphere_session", _session), patch.object(
        inventory_api, "vsphere", fake_vsphere
    ):
        res = _client().get("/api/inventory/datastore-clusters")
    assert res.status_code == 200
    assert "YAVIN-DEV" in res.json()


def test_clusters_vsphere_unreachable_is_502():
    @contextmanager
    def _boom():
        raise RuntimeError(
            "vSphere credentials are not configured. Set VSPHERE_SERVER."
        )
        yield  # pragma: no cover

    with patch.object(inventory_api, "vsphere_session", _boom):
        res = _client().get("/api/inventory/clusters")
    assert res.status_code == 502
    assert "vSphere" in res.json()["detail"]
    assert "Production Cluster" not in res.text


def test_viewer_forbidden():
    res = _client(Role.viewer).get("/api/inventory/clusters")
    assert res.status_code == 403


def test_unauthenticated_401():
    app = FastAPI()
    app.include_router(inventory_api.router, prefix="/api/inventory")
    res = TestClient(app).get("/api/inventory/clusters")
    assert res.status_code == 401


def test_admin_can_read_inventory():
    fake_vsphere = MagicMock()
    fake_vsphere.list_networks.return_value = ["VM Dev"]
    with patch.object(inventory_api, "vsphere_session", _session), patch.object(
        inventory_api, "vsphere", fake_vsphere
    ):
        res = _client(Role.admin).get("/api/inventory/networks")
    assert res.status_code == 200
    assert res.json() == ["VM Dev"]
