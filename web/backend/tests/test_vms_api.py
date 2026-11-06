"""VM lifecycle API (issue #5) — mocked vSphere, sqlite job store, no vCenter."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database as db
from app.api import vms as vms_api
from app.auth import get_current_user
from app.config import get_settings
from app.models import BuildJob, BuildRequest, BuildStatus, Role, UserOut
from ovbuilder import vmops, vmstate
from ovbuilder.terraform import state_dir_for_vm

UUID_A = "4210aaaa-0000"


def _client(username: str, role: Role) -> TestClient:
    app = FastAPI()
    app.include_router(vms_api.router, prefix="/api/vms")

    async def _user() -> UserOut:
        return UserOut(username=username, role=role)

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _job(hostname: str, owner: str, status: BuildStatus = BuildStatus.succeeded, *, days_ago: int = 1) -> BuildJob:
    now = datetime.now(timezone.utc)
    job = BuildJob(
        id=str(uuid.uuid4()),
        status=status,
        request=BuildRequest(hostname=hostname, ip="10.0.0.10", os_image="ubuntu-24.04"),
        requested_by=owner,
        created_at=now - timedelta(days=days_ago, hours=1),
        finished_at=now - timedelta(days=days_ago),
        vm_name=hostname if status == BuildStatus.succeeded else None,
    )
    db.save_job_sync(job)
    return job


def _row(name: str, power: str = "poweredOn", uuid_: str = UUID_A) -> dict:
    return {"name": name, "power_state": power, "uuid": uuid_, "cpus": 2, "memory_mb": 4096}


@contextmanager
def _fake_session(settings=None):
    yield (object(), "Main DC")


@pytest.fixture
def env(job_db, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(vms_api, "vsphere_session", _fake_session)
    monkeypatch.setattr("app.vm_lifecycle.vsphere_session", _fake_session)
    return get_settings()


def _inventory(monkeypatch, rows):
    monkeypatch.setattr(vmops, "list_vms", lambda si, datacenter_name=None: [dict(r) for r in rows])


def _put_lease(vm_name: str, **fields) -> None:
    with db._sync_session()() as session:
        session.add(db.VmLeaseRow(vm_name=vm_name, updated_at=datetime.now(timezone.utc), **fields))
        session.commit()


def _lease_exists(vm_name: str) -> bool:
    with db._sync_session()() as session:
        return session.get(db.VmLeaseRow, vm_name) is not None


def _seed_state(vm_name: str, vm_uuid: str = UUID_A):
    sdir = state_dir_for_vm(vm_name)
    (sdir / "terraform.tfstate").write_text(
        json.dumps(
            {"resources": [{"type": "vsphere_virtual_machine", "instances": [{"attributes": {"uuid": vm_uuid}}]}]}
        )
    )
    return sdir


# --- listing & ownership -------------------------------------------------------


def test_builder_sees_only_own_succeeded_builds(env, monkeypatch):
    _job("alice01", "alice")
    _job("bob01", "bob")
    # A failed build of an existing VM's name must not grant access to it.
    _job("prod-db", "alice", BuildStatus.failed)
    _inventory(monkeypatch, [_row("alice01"), _row("bob01"), _row("prod-db")])

    res = _client("alice", Role.builder).get("/api/vms")
    assert res.status_code == 200
    rows = res.json()
    assert [r["name"] for r in rows] == ["alice01"]
    assert rows[0]["owner"] == "alice" and rows[0]["managed"] is True


def test_admin_sees_all_with_ownership(env, monkeypatch):
    _job("alice01", "alice")
    _inventory(monkeypatch, [_row("alice01"), _row("foreign")])
    rows = {r["name"]: r for r in _client("root", Role.admin).get("/api/vms").json()}
    assert rows["alice01"]["owner"] == "alice"
    assert rows["foreign"]["managed"] is False and rows["foreign"]["owner"] is None


def test_viewer_cannot_list(env, monkeypatch):
    _inventory(monkeypatch, [])
    assert _client("v", Role.viewer).get("/api/vms").status_code == 403


# --- power & error mapping ------------------------------------------------------


def test_power_off_owner_only(env, monkeypatch):
    _job("alice01", "alice")
    calls = []
    monkeypatch.setattr(
        vmops, "power_off", lambda si, name, datacenter_name=None: calls.append((name, datacenter_name)) or {"changed": True}
    )
    assert _client("bob", Role.builder).post("/api/vms/alice01/power-off").status_code == 403
    assert _client("alice", Role.builder).post("/api/vms/alice01/power-off").status_code == 200
    assert calls == [("alice01", "Main DC")]


@pytest.mark.parametrize(
    "exc, code",
    [
        (vmops.VmNotFoundError("gone"), 404),
        (vmops.VmStateError("not powered on"), 409),
        (ValueError("bad"), 400),
        (RuntimeError("vCenter down"), 502),
    ],
)
def test_errors_map_to_status(env, monkeypatch, exc, code):
    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(vmops, "reset", boom)
    assert _client("root", Role.admin).post("/api/vms/web01/reset").status_code == code


def test_mutation_refused_while_build_holds_hostname_lock(env, monkeypatch):
    _job("alice01", "alice")
    monkeypatch.setattr(vmops, "power_off", lambda *a, **k: {"changed": True})
    sdir = state_dir_for_vm("alice01")
    with vmstate.hostname_lock(sdir):
        res = _client("alice", Role.builder).post("/api/vms/alice01/power-off")
    assert res.status_code == 409
    assert "alice01" in res.json()["detail"]


# --- snapshots & resize -----------------------------------------------------------


def test_snapshot_lifecycle_routes(env, monkeypatch):
    _job("alice01", "alice")
    seen = {}

    def record(key, value):
        seen.setdefault(key, value)
        return {"changed": True}

    monkeypatch.setattr(vmops, "list_snapshots", lambda si, n, datacenter_name=None: [{"id": "snapshot-1"}])
    monkeypatch.setattr(
        vmops, "create_snapshot",
        lambda si, n, name, desc, memory, datacenter_name=None: record("create", (name, memory)),
    )
    monkeypatch.setattr(
        vmops, "revert_snapshot", lambda si, n, sid, datacenter_name=None: record("revert", sid)
    )
    monkeypatch.setattr(
        vmops, "delete_snapshot",
        lambda si, n, sid, children, datacenter_name=None: record("delete", (sid, children)),
    )
    c = _client("alice", Role.builder)
    assert c.get("/api/vms/alice01/snapshots").json() == [{"id": "snapshot-1"}]
    assert c.post("/api/vms/alice01/snapshots", json={"name": "pre", "memory": True}).status_code == 200
    assert c.post("/api/vms/alice01/snapshot", json={"name": "legacy-path"}).status_code == 200
    assert c.post("/api/vms/alice01/snapshots/snapshot-1/revert").status_code == 200
    assert c.delete("/api/vms/alice01/snapshots/snapshot-1?remove_children=true").status_code == 200
    assert seen == {"create": ("pre", True), "revert": "snapshot-1", "delete": ("snapshot-1", True)}


def test_resize_converts_memory_and_requires_a_change(env, monkeypatch):
    _job("alice01", "alice")
    seen = {}

    def fake_resize(si, name, **kwargs):
        seen.update(kwargs)
        return {"changed": True}

    monkeypatch.setattr(vmops, "resize_vm", fake_resize)
    c = _client("alice", Role.builder)
    assert c.post("/api/vms/alice01/resize", json={}).status_code == 400
    assert c.post("/api/vms/alice01/resize", json={"cpus": 4, "memory_gb": 8}).status_code == 200
    assert seen["cpus"] == 4 and seen["memory_mb"] == 8192 and seen["disk_gb"] is None


# --- destroy ------------------------------------------------------------------


def test_destroy_requires_confirm_and_retires_state(env, monkeypatch):
    _job("alice01", "alice")
    calls = []
    monkeypatch.setattr(
        vmstate, "destroy_with_state",
        lambda si, name, dc: calls.append(name) or {"name": name, "action": "destroyed"},
    )
    c = _client("alice", Role.builder)
    assert c.delete("/api/vms/alice01").status_code == 422
    assert c.delete("/api/vms/alice01?confirm=nope").status_code == 400
    assert _client("bob", Role.builder).delete("/api/vms/alice01?confirm=alice01").status_code == 403
    assert c.delete("/api/vms/alice01?confirm=alice01").status_code == 200
    assert calls == ["alice01"]


def test_destroy_deletes_lease(env, monkeypatch):
    _job("alice01", "alice")
    _put_lease("alice01", explicit=True, expires_at=None)
    monkeypatch.setattr(vmstate, "destroy_with_state", lambda si, name, dc: {"name": name})
    assert _client("alice", Role.builder).delete("/api/vms/alice01?confirm=alice01").status_code == 200
    assert not _lease_exists("alice01")


def test_builder_destroy_can_be_disabled(env, monkeypatch):
    _job("alice01", "alice")
    monkeypatch.setattr(env, "vm_builder_can_destroy", False)
    monkeypatch.setattr(vmstate, "destroy_with_state", lambda si, name, dc: {"name": name})
    assert _client("alice", Role.builder).delete("/api/vms/alice01?confirm=alice01").status_code == 403
    assert _client("root", Role.admin).delete("/api/vms/alice01?confirm=alice01").status_code == 200


# --- leftovers of failed / cancelled builds -------------------------------------


def test_cancelled_build_leftover_listed_and_destroyable_when_state_proves_it(env, monkeypatch):
    _job("half01", "alice", BuildStatus.cancelled)
    _seed_state("half01", UUID_A)
    _inventory(monkeypatch, [_row("half01", uuid_=UUID_A.upper())])
    monkeypatch.setattr(vmops, "vm_uuid", lambda si, name, dc=None: UUID_A)
    destroyed = []
    monkeypatch.setattr(vmstate, "destroy_with_state", lambda si, name, dc: destroyed.append(name) or {"name": name})

    c = _client("alice", Role.builder)
    rows = c.get("/api/vms").json()
    assert [(r["name"], r["orphan"], r["managed"]) for r in rows] == [("half01", True, False)]
    # Only cleanup is allowed on a leftover, not other lifecycle actions.
    assert c.post("/api/vms/half01/power-off").status_code == 403
    assert c.delete("/api/vms/half01?confirm=half01").status_code == 200
    assert destroyed == ["half01"]


def test_leftover_destroy_refused_when_uuid_does_not_match(env, monkeypatch):
    _job("half01", "alice", BuildStatus.failed)
    _seed_state("half01", UUID_A)
    monkeypatch.setattr(vmops, "vm_uuid", lambda si, name, dc=None: "4210ffff-9999")
    monkeypatch.setattr(vmstate, "destroy_with_state", lambda *a: pytest.fail("must not destroy"))
    assert _client("alice", Role.builder).delete("/api/vms/half01?confirm=half01").status_code == 409


def test_failed_build_without_state_grants_nothing(env, monkeypatch):
    _job("prod-db", "alice", BuildStatus.failed)
    _inventory(monkeypatch, [_row("prod-db")])
    c = _client("alice", Role.builder)
    assert c.get("/api/vms").json() == []
    assert c.delete("/api/vms/prod-db?confirm=prod-db").status_code == 403


# --- leases ----------------------------------------------------------------------


def test_lease_limits_for_builders(env, monkeypatch):
    _job("alice01", "alice")
    monkeypatch.setattr(env, "vm_lease_max_days", 30)
    c = _client("alice", Role.builder)
    now = datetime.now(timezone.utc)
    ok = c.put("/api/vms/alice01/lease", json={"expires_at": (now + timedelta(days=10)).isoformat()})
    assert ok.status_code == 200 and ok.json()["explicit"] is True
    assert c.put("/api/vms/alice01/lease", json={"expires_at": (now + timedelta(days=60)).isoformat()}).status_code == 400
    assert c.put("/api/vms/alice01/lease", json={"expires_at": (now - timedelta(days=1)).isoformat()}).status_code == 400
    assert c.put("/api/vms/alice01/lease", json={"expires_at": None}).status_code == 403
    admin = _client("root", Role.admin).put("/api/vms/alice01/lease", json={"expires_at": None})
    assert admin.status_code == 200 and admin.json()["expires_at"] is None


def test_default_lease_from_policy(env, monkeypatch):
    _job("alice01", "alice", days_ago=2)
    monkeypatch.setattr(env, "vm_lease_days", 7)
    body = _client("alice", Role.builder).get("/api/vms/alice01/lease").json()
    expires = datetime.fromisoformat(body["expires_at"])
    assert body["explicit"] is False
    assert timedelta(days=4) < expires - datetime.now(timezone.utc) < timedelta(days=6)


def test_unmanaged_vm_cannot_be_leased(env):
    assert _client("root", Role.admin).put("/api/vms/foreign/lease", json={"expires_at": None}).status_code == 409


# --- reclaim endpoints -------------------------------------------------------------


def test_reclaim_is_admin_only_and_preview_changes_nothing(env, monkeypatch):
    _job("old01", "alice", days_ago=40)
    monkeypatch.setattr(env, "vm_lease_days", 30)
    monkeypatch.setattr(env, "vm_reclaim_action", "destroy")
    _inventory(monkeypatch, [_row("old01")])
    monkeypatch.setattr(vmstate, "destroy_with_state", lambda *a, **k: pytest.fail("preview must not act"))

    assert _client("alice", Role.builder).get("/api/vms/reclaim/preview").status_code == 403
    report = _client("root", Role.admin).get("/api/vms/reclaim/preview").json()
    assert report["dry_run"] is True
    assert [(c["name"], c["reason"], c["action"]) for c in report["candidates"]] == [("old01", "expired", "destroy")]


def test_reclaim_now_destroys_expired(env, monkeypatch):
    _job("old01", "alice", days_ago=40)
    _job("new01", "alice", days_ago=1)
    _put_lease("old01", explicit=False)
    monkeypatch.setattr(env, "vm_lease_days", 30)
    monkeypatch.setattr(env, "vm_reclaim_action", "destroy")
    _inventory(monkeypatch, [_row("old01"), _row("new01"), _row("foreign")])
    destroyed = []
    monkeypatch.setattr(vmstate, "destroy_with_state", lambda si, name, dc=None: destroyed.append(name))

    report = _client("root", Role.admin).post("/api/vms/reclaim").json()
    assert destroyed == ["old01"]
    assert report["candidates"][0]["done"] is True
    assert not _lease_exists("old01")
