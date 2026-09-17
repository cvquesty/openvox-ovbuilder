"""Optional Slack-compatible webhook when a build finishes."""

from __future__ import annotations

import logging
from typing import Any

from .config import get_settings
from .models import BuildJob, BuildStatus

logger = logging.getLogger(__name__)


def notify_job(job: BuildJob) -> None:
    settings = get_settings()
    url = (settings.notify_webhook_url or "").strip()
    if not url:
        return
    if job.status == BuildStatus.succeeded and not settings.notify_on_success:
        return
    if job.status == BuildStatus.failed and not settings.notify_on_failure:
        return
    if job.status not in (BuildStatus.succeeded, BuildStatus.failed, BuildStatus.cancelled):
        return
    payload: dict[str, Any] = {
        "text": (
            f"OV Builder {job.status.value}: {job.request.hostname} "
            f"by {job.requested_by}"
            + (f" ({job.vm_ip})" if job.vm_ip else "")
            + (f" — {job.error}" if job.error else "")
        )
    }
    try:
        import httpx

        httpx.post(url, json=payload, timeout=8.0)
    except Exception:
        logger.warning("notify webhook failed for job %s", job.id, exc_info=True)
