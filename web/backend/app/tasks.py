"""Celery tasks: dispatch ovbuilder builds in parallel.

Each submitted build becomes an independent Celery task so multiple operators
can build at once without colliding on Terraform state or vCenter sessions.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from celery import Celery

from ovbuilder.placement import datastore_cluster_for

from .config import get_settings
from .database import get_job_sync, save_job_sync, update_job_log_sync
from .models import BuildStatus
from .notify import notify_job

settings = get_settings()

# Names the CLI / Terraform already honor. Never put the password on argv.
_VSPHERE_PASSWORD_ENV = (
    "VSPHERE_PASSWORD",
    "TF_VAR_vsphere_password",
    "OVBUILDER_VSPHERE_PASSWORD",
)

celery_app = Celery(
    "ovbuilder_web",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_track_started=True,
    task_time_limit=settings.build_time_limit_seconds,
    worker_concurrency=settings.max_concurrent_builds,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

_LOG_PERSIST_INTERVAL = 25


def _build_command(req: Dict[str, Any], job_id: str) -> tuple[list[str], dict]:
    env = (req.get("environment") or "dev").lower()
    datastore_cluster = datastore_cluster_for(env)

    cmd: list[str] = [
        settings.ovbuilder_binary,
        "build",
        "--yes",
        "--hostname", req["hostname"],
        "--ip", req["ip"],
        "--os", req["os_image"],
        "--prefix", str(req.get("prefix") or "24"),
        "--cpus", str(req.get("cpus") or 2),
        "--memory", str(req.get("memory_gb") or 4),
        "--disk", str(req.get("disk_gb") or 80),
    ]
    if datastore_cluster:
        cmd += ["--vm-datastore-cluster", datastore_cluster]
    if req.get("cluster"):
        cmd += ["--cluster", req["cluster"]]
    if req.get("network"):
        cmd += ["--network", req["network"]]
    for extra in req.get("networks") or []:
        if extra and extra != req.get("network"):
            cmd += ["--network", extra]

    if req.get("gateway"):
        cmd += ["--gateway", req["gateway"]]
    if req.get("dns"):
        cmd += ["--dns", ",".join(req["dns"])]
    if req.get("location"):
        cmd += ["--location", req["location"]]
    if req.get("vsphere_server"):
        cmd += ["--vsphere-server", req["vsphere_server"]]
    if req.get("vsphere_user"):
        cmd += ["--vsphere-user", req["vsphere_user"]]
    if req.get("skip_dnf_groups"):
        cmd += ["--skip-dnf-groups"]

    env_vars = os.environ.copy()
    env_vars["HOME"] = settings.ovbuilder_home
    env_vars["XDG_CONFIG_HOME"] = os.path.join(settings.ovbuilder_home, ".config")
    env_vars["XDG_DATA_HOME"] = os.path.join(settings.ovbuilder_home, ".local/share")
    password = req.get("vsphere_password")
    if password:
        for key in _VSPHERE_PASSWORD_ENV:
            env_vars[key] = password

    return cmd, env_vars


def _mark_cancelled(job, log_lines: Optional[list[str]] = None):
    job.status = BuildStatus.cancelled
    job.error = job.error or "Cancelled by user"
    job.finished_at = datetime.now(timezone.utc)
    if log_lines:
        job.log_tail = "\n".join(log_lines)
    save_job_sync(job)
    try:
        notify_job(job)
    except Exception:
        pass
    return {"job_id": job.id, "status": job.status.value}


@celery_app.task(name="ovbuilder.run_build", bind=True)
def run_build(self, job_id: str, req: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
    job = get_job_sync(job_id)
    if job is None:
        return {"job_id": job_id, "status": "missing"}

    if job.status in (BuildStatus.cancelled, BuildStatus.cancelling):
        return _mark_cancelled(job)

    job.status = BuildStatus.running
    job.started_at = datetime.now(timezone.utc)
    job.celery_task_id = self.request.id
    save_job_sync(job)

    cmd, env_vars = _build_command(req, job_id)
    log_lines: list[str] = []
    lines_since_persist = 0
    proc = None
    rc = 1

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env_vars,
            cwd=settings.ovbuilder_home,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            latest = get_job_sync(job_id)
            if latest is not None and latest.status in (BuildStatus.cancelled, BuildStatus.cancelling):
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                job = latest
                return _mark_cancelled(job, log_lines)

            log_lines.append(line.rstrip("\n"))
            if len(log_lines) > 200:
                log_lines = log_lines[-200:]
            job.log_tail = "\n".join(log_lines)
            lines_since_persist += 1
            if lines_since_persist >= _LOG_PERSIST_INTERVAL:
                # Log-only write: a full-row save would clobber cancel status
                # set by the API in another process.
                update_job_log_sync(job_id, job.log_tail)
                lines_since_persist = 0
        rc = proc.wait()
    except FileNotFoundError:
        job.status = BuildStatus.failed
        job.error = f"ovbuilder binary not found: {settings.ovbuilder_binary}"
        job.finished_at = datetime.now(timezone.utc)
        save_job_sync(job)
        notify_job(job)
        return {"job_id": job_id, "status": job.status.value, "error": job.error}
    except Exception as exc:  # noqa: BLE001
        job.status = BuildStatus.failed
        job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        save_job_sync(job)
        notify_job(job)
        return {"job_id": job_id, "status": job.status.value, "error": job.error}

    latest = get_job_sync(job_id)
    if latest is not None and latest.status in (BuildStatus.cancelled, BuildStatus.cancelling):
        job = latest
        return _mark_cancelled(job, log_lines)

    job.finished_at = datetime.now(timezone.utc)
    if rc == 0:
        job.status = BuildStatus.succeeded
        job.vm_ip = _extract_ip(log_lines)
        job.vm_name = req.get("hostname")
    else:
        job.status = BuildStatus.failed
        job.error = f"ovbuilder exited with code {rc}"
    save_job_sync(job)
    notify_job(job)
    return {"job_id": job_id, "status": job.status.value, "rc": rc}


def _extract_ip(lines: list[str]) -> Optional[str]:
    ip_re = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    for line in reversed(lines):
        m = ip_re.search(line)
        if m:
            return m.group(0)
    return None
