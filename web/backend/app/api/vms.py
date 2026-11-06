"""VM lifecycle API: list, power, snapshots, resize, leases, destroy, reclaim.

vSphere calls block for up to minutes (snapshots, reverts), so each one runs
in a worker thread with its own session instead of on the event loop.
Mutating calls on a VM ovbuilder built take the build's per-hostname lock
(``ovbuilder.vmstate.vm_lock``) and fail with 409 while a build of that
hostname is applying.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..auth import require_admin, require_builder
from ..config import get_settings
from ..database import delete_lease, get_lease, list_jobs, list_leases, update_lease
from ..models import Role, UserOut
from ..vm_lifecycle import OwnedVm, effective_expiry, owned_vms, partial_builds, run_reclaim
from ..vsphere_client import vsphere_session

logger = logging.getLogger(__name__)
router = APIRouter()


class SnapshotRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field("", max_length=500)
    memory: bool = False


class ResizeRequest(BaseModel):
    cpus: Optional[int] = Field(None, ge=1, le=128)
    memory_gb: Optional[int] = Field(None, ge=1, le=4096)
    disk_gb: Optional[int] = Field(None, ge=1)
    disk_index: int = Field(0, ge=0)


class LeaseUpdate(BaseModel):
    """``expires_at: null`` means "never expires" (admins only)."""

    expires_at: Optional[datetime] = None


VsphereCall = Callable[[Any, Optional[str]], Any]


def _in_thread(call: VsphereCall, lock_name: Optional[str]) -> Any:
    from ovbuilder.vmstate import vm_lock

    if lock_name is None:
        with vsphere_session() as (si, datacenter):
            return call(si, datacenter or None)
    with vm_lock(lock_name, create=False):
        with vsphere_session() as (si, datacenter):
            return call(si, datacenter or None)


async def _run(call: VsphereCall, *, lock_name: Optional[str] = None) -> Any:
    from ovbuilder.vmstate import StateLockedError
    from ovbuilder.vmops import VmNotFoundError, VmStateError

    try:
        return await run_in_threadpool(_in_thread, call, lock_name)
    except VmNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (VmStateError, StateLockedError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        logger.exception("vSphere call failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


def _op(fn_name: str, *args: Any, **kwargs: Any) -> VsphereCall:
    def call(si, datacenter):
        from ovbuilder import vmops

        return getattr(vmops, fn_name)(si, *args, datacenter_name=datacenter, **kwargs)

    return call


async def _owned() -> Dict[str, OwnedVm]:
    return owned_vms(await list_jobs())


async def _assert_can_touch(user: UserOut, vm_name: str) -> Optional[OwnedVm]:
    """Admins may act on any VM; builders only on VMs their builds created."""
    info = (await _owned()).get(vm_name)
    if user.role == Role.admin:
        return info
    if info is None or info.owner != user.username:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your VM")
    return info


def _audit(user: UserOut, action: str, vm_name: str, **extra: Any) -> None:
    detail = " ".join(f"{k}={v}" for k, v in extra.items())
    logger.info("vm %s %s by %s %s", action, vm_name, user.username, detail)


def _state_uuids(vm_name: str) -> Set[str]:
    from ovbuilder.vmstate import state_vm_uuids

    try:
        return state_vm_uuids(vm_name)
    except Exception:  # noqa: BLE001 — unreadable state just means "unverified"
        logger.debug("could not read Terraform state for %s", vm_name, exc_info=True)
        return set()


def _lease_out(vm_name: str, lease, info: Optional[OwnedVm]) -> Dict[str, Any]:
    settings = get_settings()
    return {
        "vm_name": vm_name,
        "owner": info.owner if info else None,
        "managed": info is not None,
        "expires_at": effective_expiry(lease, info, settings),
        "explicit": bool(lease and lease.explicit),
        "powered_off_since": lease.powered_off_since if lease else None,
        "last_action": lease.last_action if lease else None,
        "last_action_at": lease.last_action_at if lease else None,
        "max_days": settings.vm_lease_max_days,
    }


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


@router.get("")
async def list_virtual_machines(user: UserOut = Depends(require_builder)) -> List[Dict[str, Any]]:
    """Visible VMs with ownership and lease.

    ``orphan`` marks a VM left by a failed or cancelled build (its UUID is in
    that build's Terraform state); its builder may only destroy it.
    """
    rows = await _run(_op("list_vms"))
    jobs = await list_jobs()
    owned = owned_vms(jobs)
    partial = partial_builds(jobs)
    leases = await list_leases()
    settings = get_settings()
    out = []
    for row in rows:
        name = row.get("name")
        info = owned.get(name)
        orphan_of = partial.get(name)
        orphan = bool(
            orphan_of and row.get("uuid") and str(row["uuid"]).lower() in _state_uuids(name)
        )
        mine = (info and info.owner == user.username) or (orphan and orphan_of.owner == user.username)
        if user.role != Role.admin and not mine:
            continue
        row["owner"] = info.owner if info else (orphan_of.owner if orphan else None)
        row["managed"] = info is not None
        row["orphan"] = orphan
        row["job_id"] = info.job_id if info else (orphan_of.job_id if orphan else None)
        row["expires_at"] = effective_expiry(leases.get(name), info, settings)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


async def _power(vm_name: str, user: UserOut, fn_name: str) -> Dict[str, Any]:
    await _assert_can_touch(user, vm_name)
    result = await _run(_op(fn_name, vm_name), lock_name=vm_name)
    _audit(user, fn_name, vm_name)
    return result


@router.post("/{vm_name}/power-on")
async def power_on(vm_name: str, user: UserOut = Depends(require_builder)) -> Dict[str, Any]:
    result = await _power(vm_name, user, "power_on")
    if await get_lease(vm_name):
        await update_lease(vm_name, powered_off_since=None)
    return result


@router.post("/{vm_name}/power-off")
async def power_off(vm_name: str, user: UserOut = Depends(require_builder)) -> Dict[str, Any]:
    return await _power(vm_name, user, "power_off")


@router.post("/{vm_name}/reboot")
async def reboot(vm_name: str, user: UserOut = Depends(require_builder)) -> Dict[str, Any]:
    return await _power(vm_name, user, "reboot")


@router.post("/{vm_name}/reset")
async def reset(vm_name: str, user: UserOut = Depends(require_builder)) -> Dict[str, Any]:
    return await _power(vm_name, user, "reset")


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@router.get("/{vm_name}/snapshots")
async def list_snapshots(vm_name: str, user: UserOut = Depends(require_builder)) -> List[Dict[str, Any]]:
    await _assert_can_touch(user, vm_name)
    return await _run(_op("list_snapshots", vm_name))


@router.post("/{vm_name}/snapshot")
@router.post("/{vm_name}/snapshots")
async def create_snapshot(
    vm_name: str,
    body: SnapshotRequest,
    user: UserOut = Depends(require_builder),
) -> Dict[str, Any]:
    await _assert_can_touch(user, vm_name)
    result = await _run(
        _op("create_snapshot", vm_name, body.name, body.description, body.memory), lock_name=vm_name
    )
    _audit(user, "snapshot", vm_name, snapshot=body.name)
    return result


@router.post("/{vm_name}/snapshots/{snapshot_id}/revert")
async def revert_snapshot(
    vm_name: str, snapshot_id: str, user: UserOut = Depends(require_builder)
) -> Dict[str, Any]:
    await _assert_can_touch(user, vm_name)
    result = await _run(_op("revert_snapshot", vm_name, snapshot_id), lock_name=vm_name)
    _audit(user, "revert", vm_name, snapshot_id=snapshot_id)
    return result


@router.delete("/{vm_name}/snapshots/{snapshot_id}")
async def delete_snapshot(
    vm_name: str,
    snapshot_id: str,
    remove_children: bool = Query(False),
    user: UserOut = Depends(require_builder),
) -> Dict[str, Any]:
    await _assert_can_touch(user, vm_name)
    result = await _run(
        _op("delete_snapshot", vm_name, snapshot_id, remove_children), lock_name=vm_name
    )
    _audit(user, "delete_snapshot", vm_name, snapshot_id=snapshot_id, remove_children=remove_children)
    return result


# ---------------------------------------------------------------------------
# Resize
# ---------------------------------------------------------------------------


@router.post("/{vm_name}/resize")
async def resize(
    vm_name: str, body: ResizeRequest, user: UserOut = Depends(require_builder)
) -> Dict[str, Any]:
    await _assert_can_touch(user, vm_name)
    if body.cpus is None and body.memory_gb is None and body.disk_gb is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set cpus, memory_gb, or disk_gb")
    result = await _run(
        _op(
            "resize_vm",
            vm_name,
            cpus=body.cpus,
            memory_mb=body.memory_gb * 1024 if body.memory_gb is not None else None,
            disk_gb=body.disk_gb,
            disk_index=body.disk_index,
        ),
        lock_name=vm_name,
    )
    _audit(user, "resize", vm_name, cpus=body.cpus, memory_gb=body.memory_gb, disk_gb=body.disk_gb)
    return result


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------


@router.get("/{vm_name}/lease")
async def read_lease(vm_name: str, user: UserOut = Depends(require_builder)) -> Dict[str, Any]:
    info = await _assert_can_touch(user, vm_name)
    return _lease_out(vm_name, await get_lease(vm_name), info)


@router.put("/{vm_name}/lease")
async def set_lease(
    vm_name: str, body: LeaseUpdate, user: UserOut = Depends(require_builder)
) -> Dict[str, Any]:
    info = await _assert_can_touch(user, vm_name)
    if info is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only VMs created by an ovbuilder build can be leased"
        )
    expires = body.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if user.role != Role.admin:
        now = datetime.now(timezone.utc)
        max_days = get_settings().vm_lease_max_days
        if expires is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can remove a lease expiry")
        if expires <= now:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "expires_at must be in the future")
        if expires > now + timedelta(days=max_days):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Leases can be extended at most {max_days} days from now"
            )
    lease = await update_lease(vm_name, expires_at=expires, explicit=True, updated_by=user.username)
    _audit(user, "lease", vm_name, expires_at=expires)
    return _lease_out(vm_name, lease, info)


# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------


async def _destroy_scope(user: UserOut, vm_name: str) -> Optional[Set[str]]:
    """Authorize a destroy. Returns UUIDs the VM must match, or ``None`` for any.

    Builders may destroy VMs from their succeeded builds, or the leftover of
    their failed/cancelled build when Terraform state proves it created it.
    """
    if user.role == Role.admin:
        return None
    if not get_settings().vm_builder_can_destroy:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can destroy VMs")
    jobs = await list_jobs()
    info = owned_vms(jobs).get(vm_name)
    if info is not None:
        if info.owner != user.username:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your VM")
        return None
    partial = partial_builds(jobs).get(vm_name)
    uuids = _state_uuids(vm_name) if partial and partial.owner == user.username else set()
    if not uuids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your VM")
    return uuids


@router.delete("/{vm_name}")
async def destroy(
    vm_name: str,
    confirm: str = Query(..., description="Must equal the VM name"),
    user: UserOut = Depends(require_builder),
) -> Dict[str, Any]:
    """Destroy the VM and retire its per-VM Terraform state."""
    if confirm != vm_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "confirm must equal the VM name")
    required_uuids = await _destroy_scope(user, vm_name)

    def call(si, datacenter):
        from ovbuilder import vmops, vmstate

        if required_uuids is not None:
            uuid = vmops.vm_uuid(si, vm_name, datacenter)
            if uuid not in required_uuids:
                raise vmops.VmStateError(
                    f"{vm_name} in vCenter is not the VM this build's Terraform state created"
                )
        return vmstate.destroy_with_state(si, vm_name, datacenter)

    result = await _run(call)
    await delete_lease(vm_name)
    _audit(user, "destroy", vm_name)
    return result


# ---------------------------------------------------------------------------
# Reclaim (admin)
# ---------------------------------------------------------------------------


async def _reclaim(dry_run: bool, user: UserOut) -> Dict[str, Any]:
    try:
        return await run_reclaim(dry_run=dry_run, actor=user.username)
    except Exception as exc:
        logger.exception("reclaim failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/reclaim/preview")
async def reclaim_preview(user: UserOut = Depends(require_admin)) -> Dict[str, Any]:
    return await _reclaim(True, user)


@router.post("/reclaim")
async def reclaim_now(user: UserOut = Depends(require_admin)) -> Dict[str, Any]:
    return await _reclaim(False, user)
