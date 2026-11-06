"""Reclaim policy (issue #5): pure evaluation, no database or vCenter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import BuildJob, BuildRequest, BuildStatus, VmLease
from app.vm_lifecycle import evaluate_reclaim, owned_vms, partial_builds

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _policy(**kw):
    base = dict(vm_lease_days=0, vm_idle_days=0, vm_reclaim_action="power_off")
    base.update(kw)
    return SimpleNamespace(**base)


def _job(name, owner="alice", status=BuildStatus.succeeded, days_ago=1, job_id=None):
    at = NOW - timedelta(days=days_ago)
    return BuildJob(
        id=job_id or f"{name}-{days_ago}",
        status=status,
        request=BuildRequest(hostname=name, ip="10.0.0.1", os_image="ubuntu-24.04"),
        requested_by=owner,
        created_at=at,
        finished_at=at,
    )


def _vm(name, power="poweredOn"):
    return {"name": name, "power_state": power}


def test_latest_succeeded_build_owns_the_name():
    jobs = [_job("web01", "alice", days_ago=10), _job("web01", "bob", days_ago=2), _job("web01", "eve", BuildStatus.failed, 0)]
    owned = owned_vms(jobs)
    assert owned["web01"].owner == "bob"


def test_partial_builds_exclude_names_with_a_success():
    jobs = [_job("web01"), _job("web01", "eve", BuildStatus.cancelled, 0), _job("half", "eve", BuildStatus.failed, 0)]
    assert set(partial_builds(jobs)) == {"half"}


def test_nothing_happens_by_default():
    owned = owned_vms([_job("web01", days_ago=400)])
    cands, _ = evaluate_reclaim([_vm("web01")], owned, {}, _policy(), NOW)
    assert cands == []


def test_expired_by_default_lease():
    owned = owned_vms([_job("old", days_ago=31), _job("new", days_ago=5)])
    cands, _ = evaluate_reclaim([_vm("old"), _vm("new")], owned, {}, _policy(vm_lease_days=30), NOW)
    assert [(c.name, c.reason, c.action) for c in cands] == [("old", "expired", "power_off")]


def test_explicit_lease_overrides_policy():
    owned = owned_vms([_job("old", days_ago=31), _job("pinned", days_ago=31)])
    leases = {
        "old": VmLease(vm_name="old", explicit=True, expires_at=NOW + timedelta(days=3)),
        "pinned": VmLease(vm_name="pinned", explicit=True, expires_at=None),
    }
    cands, _ = evaluate_reclaim([_vm("old"), _vm("pinned")], owned, leases, _policy(vm_lease_days=30), NOW)
    assert cands == []


def test_unowned_vms_are_never_candidates():
    cands, tracking = evaluate_reclaim([_vm("foreign", "poweredOff")], {}, {}, _policy(vm_lease_days=1, vm_idle_days=1), NOW)
    assert cands == [] and tracking == {}


def test_idle_tracking_and_reclaim():
    owned = owned_vms([_job("web01")])
    policy = _policy(vm_idle_days=7, vm_reclaim_action="destroy")

    cands, tracking = evaluate_reclaim([_vm("web01", "poweredOff")], owned, {}, policy, NOW)
    assert cands == [] and tracking == {"web01": NOW}

    leases = {"web01": VmLease(vm_name="web01", powered_off_since=NOW - timedelta(days=8))}
    cands, tracking = evaluate_reclaim([_vm("web01", "poweredOff")], owned, leases, policy, NOW)
    assert tracking == {}
    assert [(c.reason, c.action) for c in cands] == [("idle", "destroy")]

    cands, tracking = evaluate_reclaim([_vm("web01", "poweredOn")], owned, leases, policy, NOW)
    assert cands == [] and tracking == {"web01": None}


def test_power_off_action_on_an_off_vm_is_none():
    owned = owned_vms([_job("old", days_ago=31)])
    cands, _ = evaluate_reclaim([_vm("old", "poweredOff")], owned, {}, _policy(vm_lease_days=30), NOW)
    assert [(c.reason, c.action) for c in cands] == [("expired", "none")]
