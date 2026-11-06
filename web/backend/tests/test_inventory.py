"""Inventory API tests — mocked vSphere client, no live vCenter."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
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


@pytest.fixture(autouse=True)
def _fresh_inventory_cache():
    inventory_api._cache.clear()
    yield
    inventory_api._cache.clear()


def test_environments_come_from_shared_mapping():
    res = _client().get("/api/inventory/environments")
    assert res.status_code == 200
    rows = res.json()
    keys = {row["key"] for row in rows}
    assert "dev" in keys and "prod" in keys
    by_key = {row["key"]: row for row in rows}
    assert by_key["dev"]["datastore_cluster"]
    assert by_key["prod"]["datastore_cluster"]


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
    fake_vsphere.list_standalone_hosts.assert_not_called()


def test_hosts_live_success():
    fake_vsphere = MagicMock()
    fake_vsphere.list_standalone_hosts.return_value = [
        "esx1.sea3.office.example.net",
        "esx2.sea3.office.example.net",
    ]
    with patch.object(inventory_api, "vsphere_session", _session), patch.object(
        inventory_api, "vsphere", fake_vsphere
    ):
        res = _client().get("/api/inventory/hosts")
    assert res.status_code == 200
    assert res.json() == [
        "esx1.sea3.office.example.net",
        "esx2.sea3.office.example.net",
    ]
    fake_vsphere.list_standalone_hosts.assert_called_once()
    fake_vsphere.list_clusters.assert_not_called()


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


class _CountingSession:
    """vsphere_session stand-in that counts opens and can be switched off."""

    def __init__(self, datacenter: str = "Main DC") -> None:
        self.datacenter = datacenter
        self.opens = 0
        self.down = False

    @contextmanager
    def __call__(self):
        self.opens += 1
        if self.down:
            raise RuntimeError("Failed to connect to vCenter vc.example.com: timed out")
        yield (object(), self.datacenter)


def _patched(session, fake_vsphere):
    return patch.object(inventory_api, "vsphere_session", session), patch.object(
        inventory_api, "vsphere", fake_vsphere
    )


def test_clusters_cached_between_requests():
    session = _CountingSession()
    fake_vsphere = MagicMock()
    fake_vsphere.list_clusters.return_value = ["Production Cluster"]
    p1, p2 = _patched(session, fake_vsphere)
    with p1, p2:
        client = _client()
        first = client.get("/api/inventory/clusters")
        second = client.get("/api/inventory/clusters")
    assert first.headers["X-Inventory-Source"] == "live"
    assert second.headers["X-Inventory-Source"] == "cache"
    assert second.json() == ["Production Cluster"]
    assert session.opens == 1
    fake_vsphere.list_clusters.assert_called_once()


def test_refresh_query_bypasses_cache():
    session = _CountingSession()
    fake_vsphere = MagicMock()
    fake_vsphere.list_networks.side_effect = [["VM Old"], ["VM Old", "VM New"]]
    p1, p2 = _patched(session, fake_vsphere)
    with p1, p2:
        client = _client()
        client.get("/api/inventory/networks")
        res = client.get("/api/inventory/networks?refresh=true")
    assert res.headers["X-Inventory-Source"] == "live"
    assert res.json() == ["VM Old", "VM New"]
    assert session.opens == 2


def test_endpoints_cache_independently():
    session = _CountingSession()
    fake_vsphere = MagicMock()
    fake_vsphere.list_clusters.return_value = ["C1"]
    fake_vsphere.list_networks.return_value = ["N1"]
    p1, p2 = _patched(session, fake_vsphere)
    with p1, p2:
        client = _client()
        assert client.get("/api/inventory/clusters").json() == ["C1"]
        assert client.get("/api/inventory/networks").json() == ["N1"]
    assert session.opens == 2


def test_vcenter_down_serves_stale_list(monkeypatch):
    monkeypatch.setenv("INVENTORY_CACHE_TTL_SECONDS", "0")
    inventory_api.get_settings.cache_clear()
    session = _CountingSession()
    fake_vsphere = MagicMock()
    fake_vsphere.list_clusters.return_value = ["Production Cluster"]
    p1, p2 = _patched(session, fake_vsphere)
    try:
        with p1, p2:
            client = _client()
            assert client.get("/api/inventory/clusters").status_code == 200
            session.down = True
            res = client.get("/api/inventory/clusters")
    finally:
        inventory_api.get_settings.cache_clear()
    assert res.status_code == 200
    assert res.json() == ["Production Cluster"]
    assert res.headers["X-Inventory-Source"] == "stale"


def test_vcenter_down_without_cache_is_502_with_retry_after():
    session = _CountingSession()
    session.down = True
    p1, p2 = _patched(session, MagicMock())
    with p1, p2:
        client = _client()
        first = client.get("/api/inventory/networks")
        second = client.get("/api/inventory/networks")
    assert first.status_code == 502
    assert "timed out" in first.json()["detail"]
    assert int(first.headers["Retry-After"]) > 0
    assert second.status_code == 502
    assert session.opens == 1, "error backoff must not reconnect immediately"


def test_os_images_cached_and_stale_beats_config_fallback(monkeypatch):
    monkeypatch.setenv("INVENTORY_CACHE_TTL_SECONDS", "0")
    inventory_api.get_settings.cache_clear()
    session = _CountingSession()
    live = [
        GoldenTemplate(
            name="ovbuilder-rocky-9",
            datacenter="PDXC",
            datastore="HOTH_DEV",
            key="rocky-9",
            label="Rocky 9",
            default_user="rocky",
        )
    ]
    try:
        with patch.object(inventory_api, "vsphere_session", session), patch.object(
            inventory_api, "discover_goldens", return_value=live
        ):
            client = _client()
            assert client.get("/api/inventory/os-images").json()[0]["key"] == "rocky-9"
            session.down = True
            res = client.get("/api/inventory/os-images")
    finally:
        inventory_api.get_settings.cache_clear()
    assert res.headers["X-Inventory-Source"] == "stale"
    assert [row["key"] for row in res.json()] == ["rocky-9"]


def test_os_images_config_fallback_flagged():
    session = _CountingSession()
    session.down = True
    with patch.object(inventory_api, "vsphere_session", session):
        res = _client().get("/api/inventory/os-images")
    assert res.status_code == 200
    assert res.headers["X-Inventory-Source"] == "fallback"
    assert res.json()


def test_cache_key_tracks_vcenter_identity():
    session = _CountingSession()
    fake_vsphere = MagicMock()
    fake_vsphere.list_clusters.side_effect = [["VC1 Cluster"], ["VC2 Cluster"]]
    identity = ["vc1.example.com", "svc", "DC"]
    p1, p2 = _patched(session, fake_vsphere)
    with p1, p2, patch.object(
        inventory_api, "session_identity", lambda _settings=None: tuple(identity)
    ):
        client = _client()
        assert client.get("/api/inventory/clusters").json() == ["VC1 Cluster"]
        identity[0] = "vc2.example.com"
        assert client.get("/api/inventory/clusters").json() == ["VC2 Cluster"]
    assert session.opens == 2
