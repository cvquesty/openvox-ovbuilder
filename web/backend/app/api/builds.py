"""Build submission + status polling.

vSphere credentials are stripped from every response the API returns to the
browser. They are only ever consumed server-side by the Celery worker.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..auth import get_current_user, require_builder
from ..database import get_job, list_jobs, save_job
from ..models import BuildJob, BuildRequest, BuildStatus, Role, UserOut
from ..tasks import run_build

router = APIRouter()


class BuildJobPublic(BaseModel):
    """BuildJob with secrets redacted for the browser."""
    id: str
    status: BuildStatus
    request: BuildRequest
    requested_by: str
    created_at: datetime
    started_at: Optional_dt = None
    finished_at: Optional_dt = None
    celery_task_id: Optional[str] = None
    log_tail: str = ""
    error: Optional[str] = None
    vm_ip: Optional[str] = None
    vm_name: Optional[str] = None

    @classmethod
    def from_job(cls, job: BuildJob) -> "BuildJobPublic":
        req = job.request.model_dump()
        # Never leak the vSphere password (or any secret) to the client.
        req["vsphere_password"] = None
        return cls(
            id=job.id,
            status=job.status,
            request=BuildRequest(**req),
            requested_by=job.requested_by,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            celery_task_id=job.celery_task_id,
            log_tail=job.log_tail,
            error=job.error,
            vm_ip=job.vm_ip,
            vm_name=job.vm_name,
        )


# Local alias so the annotation above resolves without importing Optional at runtime oddly.
from typing import Optional as Optional_dt  # noqa: E402


@router.post("", response_model=BuildJobPublic, status_code=status.HTTP_202_ACCEPTED)
async def submit_build(
    req: BuildRequest,
    user: UserOut = Depends(require_builder),
):
    job = BuildJob(
        id=str(uuid.uuid4()),
        status=BuildStatus.queued,
        request=req,
        requested_by=user.username,
        created_at=datetime.now(timezone.utc),
    )
    await save_job(job)
    task = run_build.delay(job.id, req.model_dump(), user.username)
    job.celery_task_id = task.id
    await save_job(job)
    return BuildJobPublic.from_job(job)


@router.get("", response_model=list[BuildJobPublic])
async def my_builds(user: UserOut = Depends(get_current_user)):
    if user.role == Role.admin:
        jobs = await list_jobs()
    else:
        jobs = await list_jobs(username=user.username)
    return [BuildJobPublic.from_job(j) for j in jobs]


@router.get("/{job_id}", response_model=BuildJobPublic)
async def build_status(job_id: str, user: UserOut = Depends(get_current_user)):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if user.role != Role.admin and job.requested_by != user.username:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your job")
    return BuildJobPublic.from_job(job)
