"""VM ownership, leases, and policy-driven reclaim.

Ownership comes from build history: a VM belongs to whoever ran the latest
*succeeded* build that created it. Failed or cancelled builds never confer
ownership, so a builder cannot claim an existing VM by submitting a build
with its hostname. Reclaim only ever considers owned (ovbuilder-built) VMs.

Run the scheduled reclaim with ``python -m app.vm_lifecycle`` (see
``web/systemd/ovbuilder-reclaim.timer``); ``--dry-run`` only reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import Settings, get_settings
from .database import delete_lease, list_jobs, list_leases, update_lease
from .models import BuildJob, BuildStatus, VmLease
from .vsphere_client import vsphere_session

logger = logging.getLogger(__name__)

REASON_EXPIRED = "expired"
REASON_IDLE = "idle"
ACTION_NONE = "none"


@dataclass
class OwnedVm:
    name: str
    owner: str
    job_id: str
    built_at: Optional[datetime]


@dataclass
class ReclaimCandidate:
    name: str
    owner: str
    reason: str
    action: str
    power_state: str
    expires_at: Optional[datetime]
    powered_off_since: Optional[datetime]
    error: Optional[str] = None
    done: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def built_vm_name(job: BuildJob) -> Optional[str]:
    return job.vm_name or (job.request.hostname if job.request else None)


def owned_vms(jobs: Iterable[BuildJob]) -> Dict[str, OwnedVm]:
    """Map VM name -> owner from the latest succeeded build of that name."""
    owned: Dict[str, OwnedVm] = {}
    for job in jobs:
        if job.status != BuildStatus.succeeded:
            continue
        name = built_vm_name(job)
        if not name:
            continue
        built_at = job.finished_at or job.created_at
        prior = owned.get(name)
        if prior is None or (built_at and prior.built_at and built_at > prior.built_at):
            owned[name] = OwnedVm(name=name, owner=job.requested_by, job_id=job.id, built_at=built_at)
    return owned


_PARTIAL_STATUSES = (BuildStatus.failed, BuildStatus.cancelled)


def partial_builds(jobs: Iterable[BuildJob]) -> Dict[str, OwnedVm]:
    """Map VM name -> owner of the latest failed/cancelled build of that name.

    These do not confer ownership on their own: a VM with this name only
    counts as the build's leftover when its UUID is in the build's local
    Terraform state (``ovbuilder.vmstate.state_vm_uuids``).
    """
    jobs = list(jobs)
    succeeded = owned_vms(jobs)
    partial: Dict[str, OwnedVm] = {}
    for job in jobs:
        if job.status not in _PARTIAL_STATUSES:
            continue
        name = built_vm_name(job)
        if not name or name in succeeded:
            continue
        at = job.finished_at or job.created_at
        prior = partial.get(name)
        if prior is None or (at and prior.built_at and at > prior.built_at):
            partial[name] = OwnedVm(name=name, owner=job.requested_by, job_id=job.id, built_at=at)
    return partial


def effective_expiry(
    lease: Optional[VmLease], owned: Optional[OwnedVm], settings: Settings
) -> Optional[datetime]:
    if lease and lease.explicit:
        return lease.expires_at
    if settings.vm_lease_days > 0 and owned and owned.built_at:
        return owned.built_at + timedelta(days=settings.vm_lease_days)
    return None


def evaluate_reclaim(
    vms: Iterable[Dict[str, Any]],
    owned: Dict[str, OwnedVm],
    leases: Dict[str, VmLease],
    settings: Settings,
    now: datetime,
) -> Tuple[List[ReclaimCandidate], Dict[str, Optional[datetime]]]:
    """Pure policy step.

    Returns ``(candidates, tracking)`` where *tracking* maps VM name to the
    new ``powered_off_since`` value for VMs whose observed power state
    changed since the last scan.
    """
    candidates: List[ReclaimCandidate] = []
    tracking: Dict[str, Optional[datetime]] = {}
    idle_after = timedelta(days=settings.vm_idle_days) if settings.vm_idle_days > 0 else None

    for row in vms:
        name = row.get("name")
        info = owned.get(name) if name else None
        if info is None:
            continue
        lease = leases.get(name)
        power = str(row.get("power_state") or "")
        off = power == "poweredOff"
        since = lease.powered_off_since if lease else None
        if off and since is None:
            since = now
            tracking[name] = now
        elif not off and since is not None:
            since = None
            tracking[name] = None

        expires = effective_expiry(lease, info, settings)
        if expires is not None and expires <= now:
            reason = REASON_EXPIRED
        elif idle_after is not None and since is not None and now - since >= idle_after:
            reason = REASON_IDLE
        else:
            continue

        action = settings.vm_reclaim_action
        if action == "power_off" and off:
            action = ACTION_NONE
        candidates.append(
            ReclaimCandidate(
                name=name,
                owner=info.owner,
                reason=reason,
                action=action,
                power_state=power,
                expires_at=expires,
                powered_off_since=since,
            )
        )
    return candidates, tracking


def _scan_vms() -> List[Dict[str, Any]]:
    from ovbuilder import vmops

    with vsphere_session() as (si, datacenter):
        return vmops.list_vms(si, datacenter or None)


def _apply_actions(candidates: List[ReclaimCandidate]) -> None:
    from ovbuilder import vmops, vmstate

    with vsphere_session() as (si, datacenter):
        for cand in candidates:
            try:
                if cand.action == "destroy":
                    vmstate.destroy_with_state(si, cand.name, datacenter or None)
                elif cand.action == "power_off":
                    with vmstate.vm_lock(cand.name, create=False):
                        vmops.power_off(si, cand.name, datacenter or None)
                cand.done = True
            except Exception as exc:  # noqa: BLE001 — one VM must not stop the sweep
                logger.exception("reclaim %s failed for %s", cand.action, cand.name)
                cand.error = str(exc)


async def run_reclaim(*, dry_run: bool, actor: str = "reclaim") -> Dict[str, Any]:
    """Scan owned VMs, update power-off tracking, and (unless dry run) act.

    Power-off observations are recorded even on a dry run: they are what
    makes idle detection possible and change nothing in vCenter.
    """
    settings = get_settings()
    owned = owned_vms(await list_jobs())
    leases = await list_leases()
    rows = await asyncio.to_thread(_scan_vms)
    now = _utcnow()
    candidates, tracking = evaluate_reclaim(rows, owned, leases, settings, now)

    for name, since in tracking.items():
        await update_lease(name, powered_off_since=since)

    actionable = [c for c in candidates if c.action != ACTION_NONE]
    if not dry_run and actionable:
        await asyncio.to_thread(_apply_actions, actionable)
        for cand in actionable:
            if not cand.done:
                continue
            logger.warning(
                "reclaim: %s %s (owner=%s reason=%s actor=%s)",
                cand.action, cand.name, cand.owner, cand.reason, actor,
            )
            if cand.action == "destroy":
                await delete_lease(cand.name)
            else:
                await update_lease(
                    cand.name,
                    powered_off_since=now,
                    last_action=f"reclaim_{cand.action}",
                    last_action_at=now,
                    updated_by=actor,
                )

    return {
        "dry_run": dry_run,
        "enabled": settings.vm_reclaim_enabled,
        "policy": {
            "lease_days": settings.vm_lease_days,
            "idle_days": settings.vm_idle_days,
            "action": settings.vm_reclaim_action,
        },
        "scanned": len(rows),
        "candidates": [asdict(c) for c in candidates],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Reclaim expired or idle ovbuilder VMs.")
    parser.add_argument("--dry-run", action="store_true", help="Report only; change nothing in vCenter.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    dry_run = args.dry_run or not settings.vm_reclaim_enabled
    if dry_run and not args.dry_run:
        logger.info("VM_RECLAIM_ENABLED is false; running as a dry run")
    result = asyncio.run(run_reclaim(dry_run=dry_run, actor="reclaim-timer"))
    print(json.dumps(result, default=str, indent=2))
    return 1 if any(c["error"] for c in result["candidates"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
