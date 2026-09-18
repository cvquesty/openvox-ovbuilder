"""Build submission + status polling + cancel.

vSphere passwords are accepted on submit and stored on the job row so the
Celery worker can load them by ``job_id``. They never appear on API responses.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user, require_builder
from ..config import get_settings
from ..database import count_jobs, get_job, list_jobs, save_job
from ..models import (
    ACTIVE_BUILD_STATUSES,
    BuildJob,
    BuildJobPublic,
    BuildRequest,
    BuildStatus,
    Role,
    UserOut,
)
from ..tasks import celery_app, run_build

router = APIRouter()


@router.post("", response_model=BuildJobPublic, status_code=status.HTTP_202_ACCEPTED)
async def submit_build(
    req: BuildRequest,
    user: UserOut = Depends(require_builder),
):
    settings = get_settings()
    user_active = await count_jobs(username=user.username, statuses=ACTIVE_BUILD_STATUSES)
    if user_active >= settings.max_queued_per_user:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You already have {user_active} active builds (cap {settings.max_queued_per_user})",
        )

    global_active = await count_jobs(statuses=ACTIVE_BUILD_STATUSES)
    host_cap = settings.max_concurrent_builds + settings.max_queue_depth
    if global_active >= host_cap:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Build queue is full ({global_active} active; cap {host_cap})",
        )

    job = BuildJob(
        id=str(uuid.uuid4()),
        status=BuildStatus.queued,
        request=req,
        requested_by=user.username,
        created_at=datetime.now(timezone.utc),
    )
    await save_job(job)
    # Worker loads the request (including credentials) from Postgres.
    task = run_build.delay(job.id)
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


@router.post("/{job_id}/cancel", response_model=BuildJobPublic)
async def cancel_build(job_id: str, user: UserOut = Depends(require_builder)):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if user.role != Role.admin and job.requested_by != user.username:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your job")
    if job.status not in (BuildStatus.queued, BuildStatus.running, BuildStatus.cancelling):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot cancel a {job.status.value} job")

    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")

    job.status = BuildStatus.cancelled if job.status == BuildStatus.queued else BuildStatus.cancelling
    if job.status == BuildStatus.cancelled:
        job.finished_at = datetime.now(timezone.utc)
    job.error = "Cancelled by user"
    await save_job(job)
    return BuildJobPublic.from_job(job)
