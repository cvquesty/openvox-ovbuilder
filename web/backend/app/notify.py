"""Optional build notifications: webhook (Slack or JSON) and SMTP email.

Every channel is off until configured. Settings come from the admin runtime
settings, falling back to env (.env). ``notify_job`` never raises: a broken
webhook, SMTP server, or template must not change a build's outcome.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from string import Template
from typing import Any, Optional

from .config import get_settings
from .database import get_notification_prefs_sync, get_user_email_sync
from .models import BuildJob, BuildStatus, NotificationPrefs
from .runtime_settings import RuntimeSettings, effective_value, load_runtime_settings

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = (BuildStatus.succeeded, BuildStatus.failed, BuildStatus.cancelled)

DEFAULT_TEXT_TEMPLATE = "OV Builder $status: $hostname by $requested_by$details$job_link"
DEFAULT_EMAIL_SUBJECT_TEMPLATE = "[OV Builder] $hostname $status"
DEFAULT_EMAIL_BODY_TEMPLATE = """\
Build $status for $hostname.

Job:          $job_id
Requested by: $requested_by
OS image:     $os_image
Environment:  $environment
Requested IP: $ip
VM IP:        $vm_ip
Started:      $started_at
Finished:     $finished_at
Duration:     $duration
Error:        $error

$job_url
"""


@dataclass(frozen=True)
class NotifyConfig:
    on_success: bool = True
    on_failure: bool = True
    on_cancelled: bool = True
    public_base_url: str = ""
    text_template: str = DEFAULT_TEXT_TEMPLATE
    subject_template: str = DEFAULT_EMAIL_SUBJECT_TEMPLATE
    body_template: str = DEFAULT_EMAIL_BODY_TEMPLATE
    webhook_url: str = ""
    webhook_format: str = "slack"
    webhook_timeout: float = 8.0
    email_enabled: bool = False
    email_owner: bool = True
    email_to: tuple[str, ...] = ()
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_from: str = ""
    smtp_timeout: float = 10.0

    def wants(self, status: BuildStatus) -> bool:
        return {
            BuildStatus.succeeded: self.on_success,
            BuildStatus.failed: self.on_failure,
            BuildStatus.cancelled: self.on_cancelled,
        }.get(status, False)

    @property
    def email_ready(self) -> bool:
        return bool(self.email_enabled and self.smtp_host and self.sender)

    @property
    def sender(self) -> str:
        return clean_address(self.smtp_from) or clean_address(self.smtp_user) or ""


def clean_address(value: str) -> Optional[str]:
    """Return a bare ``user@host`` or None (blocks header injection)."""
    if not value or any(c in value for c in "\r\n"):
        return None
    _, addr = parseaddr(value.strip())
    if not addr or "@" not in addr or any(c.isspace() for c in addr):
        return None
    return addr


def _split_addresses(raw: str) -> tuple[str, ...]:
    out: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        addr = clean_address(part)
        if addr and addr.lower() not in {a.lower() for a in out}:
            out.append(addr)
    return tuple(out)


def load_config(rt: Optional[RuntimeSettings] = None) -> NotifyConfig:
    env = get_settings()
    if rt is None:
        try:
            rt = load_runtime_settings()
        except Exception:
            logger.debug("runtime settings unavailable for notify", exc_info=True)
            rt = None

    def pick(name: str) -> Any:
        return effective_value(rt, env, name)

    def toggle(name: str) -> bool:
        return bool(getattr(rt, name) if rt is not None else getattr(env, name))

    fmt = str(pick("notify_webhook_format") or "slack").lower()
    return NotifyConfig(
        on_success=toggle("notify_on_success"),
        on_failure=toggle("notify_on_failure"),
        on_cancelled=toggle("notify_on_cancelled"),
        public_base_url=str(pick("public_base_url") or "").strip().rstrip("/"),
        text_template=pick("notify_text_template") or DEFAULT_TEXT_TEMPLATE,
        subject_template=pick("notify_email_subject_template") or DEFAULT_EMAIL_SUBJECT_TEMPLATE,
        body_template=pick("notify_email_body_template") or DEFAULT_EMAIL_BODY_TEMPLATE,
        webhook_url=str(pick("notify_webhook_url") or "").strip(),
        webhook_format=fmt if fmt in ("slack", "json") else "slack",
        webhook_timeout=float(env.notify_webhook_timeout_seconds),
        email_enabled=bool(pick("notify_email_enabled")),
        email_owner=bool(pick("notify_email_owner")),
        email_to=_split_addresses(str(pick("notify_email_to") or "")),
        smtp_host=str(pick("smtp_host") or "").strip(),
        smtp_port=int(pick("smtp_port") or 587),
        smtp_user=str(pick("smtp_user") or "").strip(),
        smtp_password=str(pick("smtp_password") or ""),
        smtp_starttls=bool(pick("smtp_starttls")),
        smtp_ssl=bool(pick("smtp_ssl")),
        smtp_from=str(pick("smtp_from") or "").strip(),
        smtp_timeout=float(env.smtp_timeout_seconds),
    )


def _iso(dt) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def _duration(job: BuildJob) -> str:
    if not (job.started_at and job.finished_at):
        return ""
    seconds = max(0, int((job.finished_at - job.started_at).total_seconds()))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s" if hours else f"{minutes}m{secs:02d}s"


def build_context(job: BuildJob, cfg: NotifyConfig) -> dict[str, str]:
    """Flat string variables for templates. Never includes request secrets."""
    req = job.request
    job_url = f"{cfg.public_base_url}/jobs/{job.id}" if cfg.public_base_url else ""
    details = (f" ({job.vm_ip})" if job.vm_ip else "") + (f" — {job.error}" if job.error else "")
    return {
        "app_name": get_settings().app_name,
        "status": job.status.value,
        "job_id": job.id,
        "hostname": req.hostname,
        "ip": req.ip or "",
        "os_image": req.os_image or "",
        "environment": req.environment or "",
        "cluster": req.cluster or "",
        "requested_by": job.requested_by,
        "vm_ip": job.vm_ip or "",
        "vm_name": job.vm_name or "",
        "error": job.error or "",
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "duration": _duration(job),
        "job_url": job_url,
        "job_link": f" {job_url}" if job_url else "",
        "details": details,
    }


def render(template: str, context: dict[str, str]) -> str:
    """``$name`` / ``${name}`` substitution; unknown names are left as-is."""
    return Template(template).safe_substitute(context)


def _slack_escape(value: str) -> str:
    # Stops user-supplied fields (hostname, error) from forming <!channel>
    # mentions or links in Slack mrkdwn.
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def webhook_payload(job: BuildJob, cfg: NotifyConfig, context: dict[str, str]) -> dict[str, Any]:
    if cfg.webhook_format == "json":
        return {
            "event": f"build.{job.status.value}",
            "text": render(cfg.text_template, context),
            "job": {
                key: context[key]
                for key in (
                    "job_id", "status", "hostname", "ip", "os_image", "environment",
                    "cluster", "requested_by", "vm_ip", "vm_name", "error",
                    "created_at", "started_at", "finished_at", "duration", "job_url",
                )
            },
        }
    escaped = {k: (v if k in ("job_url", "job_link") else _slack_escape(v)) for k, v in context.items()}
    return {"text": render(cfg.text_template, escaped)}


def send_webhook(job: BuildJob, cfg: NotifyConfig, context: dict[str, str]) -> None:
    if not cfg.webhook_url.lower().startswith(("http://", "https://")):
        logger.warning("notify webhook URL must be http(s); skipping job %s", job.id)
        return
    import httpx

    resp = httpx.post(cfg.webhook_url, json=webhook_payload(job, cfg, context), timeout=cfg.webhook_timeout)
    if resp.status_code >= 400:
        logger.warning("notify webhook returned HTTP %s for job %s", resp.status_code, job.id)


def email_recipients(
    job: BuildJob,
    cfg: NotifyConfig,
    *,
    owner_email: Optional[str],
    prefs: Optional[NotificationPrefs],
) -> list[str]:
    recipients = list(cfg.email_to)
    if cfg.email_owner and owner_email and prefs is not None and prefs.email_enabled:
        wanted = {
            BuildStatus.succeeded: prefs.on_success,
            BuildStatus.failed: prefs.on_failure,
            BuildStatus.cancelled: prefs.on_cancelled,
        }.get(job.status, False)
        addr = clean_address(owner_email)
        if wanted and addr and addr.lower() not in {r.lower() for r in recipients}:
            recipients.insert(0, addr)
    return recipients


def _owner_lookup(username: str) -> tuple[Optional[str], Optional[NotificationPrefs]]:
    try:
        return get_user_email_sync(username), get_notification_prefs_sync(username)
    except Exception:
        logger.warning("could not load notification prefs for %s", username, exc_info=True)
        return None, None


def send_email(cfg: NotifyConfig, recipients: list[str], subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = " ".join(subject.split())[:250]
    msg.set_content(body)

    tls = ssl.create_default_context()
    if cfg.smtp_ssl:
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout, context=tls
        )
    else:
        client = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout)
    with client as smtp:
        if not cfg.smtp_ssl and cfg.smtp_starttls:
            smtp.starttls(context=tls)
        if cfg.smtp_user and cfg.smtp_password:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)


def notify_job(job: BuildJob) -> None:
    """Fan a finished build out to every configured channel. Never raises."""
    try:
        if job.status not in TERMINAL_STATUSES:
            return
        cfg = load_config()
        if not cfg.wants(job.status):
            return
        context = build_context(job, cfg)
    except Exception:
        logger.warning("notify setup failed for job %s", getattr(job, "id", "?"), exc_info=True)
        return

    if cfg.webhook_url:
        try:
            send_webhook(job, cfg, context)
        except Exception:
            logger.warning("notify webhook failed for job %s", job.id, exc_info=True)

    if cfg.email_ready:
        try:
            owner_email, prefs = (None, None)
            if cfg.email_owner:
                owner_email, prefs = _owner_lookup(job.requested_by)
            recipients = email_recipients(job, cfg, owner_email=owner_email, prefs=prefs)
            if recipients:
                send_email(
                    cfg,
                    recipients,
                    render(cfg.subject_template, context),
                    render(cfg.body_template, context),
                )
        except Exception:
            logger.warning("notify email failed for job %s", job.id, exc_info=True)
