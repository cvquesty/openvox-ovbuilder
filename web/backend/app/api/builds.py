"""Build submission + status polling."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user, require_builder, require_role
from ..database import get_job, list_jobs, save_job
from ..models import BuildJob, BuildRequest, BuildStatus, Role, UserOut
from ..tasks import run_build

router = APIRouter()


@router.post("", response_model=BuildJob, status_code=status.HTTP_202_ACCEPTED)
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
    return job


@router.get("", response_model=list[BuildJob])
async def my_builds(user: UserOut = Depends(get_current_user)):
    if user.role == Role.admin:
        return await list_jobs()
    return await list_jobs(username=user.username)


@router.get("/{job_id}", response_model=BuildJob)
async def build_status(job_id: str, user: UserOut = Depends(get_current_user)):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if user.role != Role.admin and job.requested_by != user.username:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your job")
    return job
