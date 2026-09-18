"""Optional Slack-compatible webhook when a build finishes."""

from __future__ import annotations

import logging
from typing import Any

from .config import get_settings
from .models import BuildJob, BuildStatus
from .runtime_settings import load_runtime_settings

logger = logging.getLogger(__name__)


def notify_job(job: BuildJob) -> None:
    env = get_settings()
    try:
        rt = load_runtime_settings()
    except Exception:
        logger.debug("runtime settings unavailable for notify", exc_info=True)
        rt = None

    url = ((rt.notify_webhook_url if rt else "") or env.notify_webhook_url or "").strip()
    if not url:
        return

    on_success = rt.notify_on_success if rt is not None else env.notify_on_success
    on_failure = rt.notify_on_failure if rt is not None else env.notify_on_failure
    on_cancelled = rt.notify_on_cancelled if rt is not None else True

    if job.status == BuildStatus.succeeded and not on_success:
        return
    if job.status == BuildStatus.failed and not on_failure:
        return
    if job.status == BuildStatus.cancelled and not on_cancelled:
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
