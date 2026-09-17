"""VM lifecycle API: list, power, reboot, snapshot, destroy."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import require_admin, require_builder
from ..config import get_settings
from ..database import list_jobs
from ..models import Role, UserOut
from ..vsphere_client import vsphere_session

logger = logging.getLogger(__name__)
router = APIRouter()


class SnapshotRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""


def _owned_names(jobs) -> set[str]:
    names: set[str] = set()
    for job in jobs:
        if job.vm_name:
            names.add(job.vm_name)
        if job.request and getattr(job.request, "hostname", None):
            names.add(job.request.hostname)
    return names


async def _assert_can_touch(user: UserOut, vm_name: str) -> None:
    if user.role == Role.admin:
        return
    jobs = await list_jobs(username=user.username)
    if vm_name not in _owned_names(jobs):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your VM")


@router.get("")
async def list_virtual_machines(user: UserOut = Depends(require_builder)) -> List[Dict[str, Any]]:
    from ovbuilder import vmops

    settings = get_settings()
    try:
        with vsphere_session(settings) as (si, datacenter):
            rows = vmops.list_vms(si, datacenter or None)
    except Exception as exc:
        logger.exception("list VMs failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if user.role == Role.admin:
        return rows
    jobs = await list_jobs(username=user.username)
    allowed = _owned_names(jobs)
    return [r for r in rows if r.get("name") in allowed]


@router.post("/{vm_name}/power-on")
async def power_on(vm_name: str, user: UserOut = Depends(require_builder)) -> Dict[str, Any]:
    from ovbuilder import vmops

    await _assert_can_touch(user, vm_name)
    try:
        with vsphere_session() as (si, _dc):
            return vmops.power_on(si, vm_name)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/{vm_name}/power-off")
async def power_off(vm_name: str, user: UserOut = Depends(require_builder)) -> Dict[str, Any]:
    from ovbuilder import vmops

    await _assert_can_touch(user, vm_name)
    try:
        with vsphere_session() as (si, _dc):
            return vmops.power_off(si, vm_name)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/{vm_name}/reboot")
async def reboot(vm_name: str, user: UserOut = Depends(require_builder)) -> Dict[str, Any]:
    from ovbuilder import vmops

    await _assert_can_touch(user, vm_name)
    try:
        with vsphere_session() as (si, _dc):
            return vmops.reboot(si, vm_name)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/{vm_name}/snapshot")
async def snapshot(
    vm_name: str,
    body: SnapshotRequest,
    user: UserOut = Depends(require_builder),
) -> Dict[str, Any]:
    from ovbuilder import vmops

    await _assert_can_touch(user, vm_name)
    try:
        with vsphere_session() as (si, _dc):
            return vmops.create_snapshot(si, vm_name, body.name, body.description)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.delete("/{vm_name}")
async def destroy(vm_name: str, user: UserOut = Depends(require_admin)) -> Dict[str, Any]:
    from ovbuilder import vmops

    try:
        with vsphere_session() as (si, _dc):
            return vmops.destroy_vm(si, vm_name)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
