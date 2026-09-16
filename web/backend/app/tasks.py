"""Celery tasks: dispatch ovbuilder builds in parallel.

Each submitted build becomes an independent Celery task so multiple operators
can build at once without colliding on Terraform state or vCenter sessions.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from celery import Celery

from .config import get_settings
from .database import get_job, save_job
from .models import BuildStatus

settings = get_settings()

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

# Environment (dev/prod) -> Storage DRS datastore cluster name.
# Mirrors web/backend/app/api/inventory.py so the mapping lives in one place.
ENVIRONMENT_DATASTORE_CLUSTERS = {
    "dev": "YAVIN-DEV",
    "prod": "YAVIN-PROD",
}


def _build_command(req: Dict[str, Any], job_id: str) -> list[str]:
    """Translate a BuildRequest dict into `ovbuilder build --yes ...` argv."""
    env = (req.get("environment") or "dev").lower()
    datastore_cluster = ENVIRONMENT_DATASTORE_CLUSTERS.get(env, ENVIRONMENT_DATASTORE_CLUSTERS["dev"])

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
        "--vm-datastore-cluster", datastore_cluster,
    ]

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
    if req.get("vsphere_password"):
        cmd += ["--vsphere-password", req["vsphere_password"]]
    if req.get("skip_dnf_groups"):
        cmd += ["--skip-dnf-groups"]

    # Propagate the service account's home so the CLI finds its config/secrets.
    env_vars = os.environ.copy()
    env_vars["HOME"] = settings.ovbuilder_home
    env_vars["XDG_CONFIG_HOME"] = os.path.join(settings.ovbuilder_home, ".config")
    env_vars["XDG_DATA_HOME"] = os.path.join(settings.ovbuilder_home, ".local/share")

    return cmd, env_vars


@celery_app.task(name="ovbuilder.run_build", bind=True)
def run_build(self, job_id: str, req: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
    """Run one ovbuilder build end-to-end, updating the job registry as we go."""
    job = get_job_sync(job_id)
    if job is None:
        return {"job_id": job_id, "status": "missing"}

    job.status = BuildStatus.running
    job.started_at = datetime.now(timezone.utc)
    job.celery_task_id = self.request.id
    save_job_sync(job)

    cmd, env_vars = _build_command(req, job_id)
    log_lines: list[str] = []

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
            log_lines.append(line.rstrip("\n"))
            # Keep only the tail so the API response stays small.
            if len(log_lines) > 200:
                log_lines = log_lines[-200:]
            job.log_tail = "\n".join(log_lines)
            save_job_sync(job)
        rc = proc.wait()
    except FileNotFoundError:
        job.status = BuildStatus.failed
        job.error = f"ovbuilder binary not found: {settings.ovbuilder_binary}"
        job.finished_at = datetime.now(timezone.utc)
        save_job_sync(job)
        return {"job_id": job_id, "status": job.status.value, "error": job.error}
    except Exception as exc:  # noqa: BLE001 - surface any build failure to the UI
        job.status = BuildStatus.failed
        job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        save_job_sync(job)
        return {"job_id": job_id, "status": job.status.value, "error": job.error}

    job.finished_at = datetime.now(timezone.utc)
    if rc == 0:
        job.status = BuildStatus.succeeded
        # Best-effort: pull the VM IP from the last lines of output.
        job.vm_ip = _extract_ip(log_lines)
        job.vm_name = req.get("hostname")
    else:
        job.status = BuildStatus.failed
        job.error = f"ovbuilder exited with code {rc}"
    save_job_sync(job)
    return {"job_id": job_id, "status": job.status.value, "rc": rc}


def _extract_ip(lines: list[str]) -> Optional[str]:
    """Grab the last IPv4-looking token from the build log, if any."""
    import re

    ip_re = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    for line in reversed(lines):
        m = ip_re.search(line)
        if m:
            return m.group(0)
    return None


# ---------------------------------------------------------------------------
# Sync wrappers around the async job store (Celery tasks run in threads).
# ---------------------------------------------------------------------------

def get_job_sync(job_id: str):
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(get_job(job_id))


def save_job_sync(job) -> None:
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(save_job(job))
