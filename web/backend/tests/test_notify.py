"""Build notifications: webhook + SMTP with mocked transports, never fatal."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app import notify
from app.config import get_settings
from app.database import (
    get_job_sync,
    save_job_sync,
    set_notification_prefs,
    upsert_user,
)
from app.models import (
    BuildJob,
    BuildRequest,
    BuildStatus,
    NotificationPrefs,
    NotificationPrefsUpdate,
    Role,
)
from app.tasks import run_build

from tests.conftest import auth_header
from tests.placeholders import placeholder_value


@pytest.fixture(autouse=True)
def _isolated_notify_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OVBUILDER_DATA_DIR", str(tmp_path / "web-data"))
    for key in (
        "NOTIFY_WEBHOOK_URL",
        "OVBUILDER_NOTIFY_WEBHOOK_URL",
        "OVBUILDER_SMTP_PASSWORD",
        "NOTIFY_WEBHOOK_FORMAT",
        "NOTIFY_EMAIL_ENABLED",
        "NOTIFY_EMAIL_TO",
        "NOTIFY_TEXT_TEMPLATE",
        "NOTIFY_EMAIL_SUBJECT_TEMPLATE",
        "SMTP_HOST",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _env(monkeypatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key.upper(), value)
    get_settings.cache_clear()


def _job(status: BuildStatus = BuildStatus.succeeded, **kwargs) -> BuildJob:
    req = BuildRequest(hostname=kwargs.pop("hostname", "web01"), ip="10.0.0.8", os_image="ubuntu-24.04")
    req.vsphere_password = placeholder_value()
    started = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    return BuildJob(
        id=kwargs.pop("id", str(uuid.uuid4())),
        status=status,
        request=req,
        requested_by=kwargs.pop("requested_by", "alice"),
        created_at=started,
        started_at=started,
        finished_at=started + timedelta(minutes=3, seconds=7),
        **kwargs,
    )


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []
    fail_send = False

    def __init__(self, host, port, timeout=None, **kwargs):
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.logged_in_as = None
        self.sent: list = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, _secret):
        self.logged_in_as = user

    def send_message(self, msg):
        if _FakeSMTP.fail_send:
            raise OSError("smtp down")
        self.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances = []
    _FakeSMTP.fail_send = False
    monkeypatch.setattr("app.notify.smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("app.notify.smtplib.SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


@pytest.fixture
def fake_post(monkeypatch):
    post = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setattr("httpx.post", post)
    return post


@pytest.fixture
def no_owner(monkeypatch):
    monkeypatch.setattr(notify, "_owner_lookup", lambda _u: (None, None))


def test_nothing_sent_by_default(fake_post, fake_smtp, no_owner):
    notify.notify_job(_job())
    fake_post.assert_not_called()
    assert fake_smtp.instances == []


def test_email_disabled_by_default_even_with_smtp_host(monkeypatch, fake_post, fake_smtp, no_owner):
    _env(monkeypatch, smtp_host="smtp.example.com", smtp_from="builder@example.com",
         notify_email_to="ops@example.com")
    notify.notify_job(_job())
    assert fake_smtp.instances == []


def test_non_terminal_status_is_ignored(monkeypatch, fake_post, no_owner):
    _env(monkeypatch, notify_webhook_url="https://example.com/webhook")
    notify.notify_job(_job(BuildStatus.running))
    fake_post.assert_not_called()


def test_slack_webhook_default_text(monkeypatch, fake_post, no_owner):
    _env(monkeypatch, notify_webhook_url="https://example.com/webhook")
    notify.notify_job(_job(BuildStatus.failed, error="ovbuilder exited with code 2"))
    fake_post.assert_called_once()
    url = fake_post.call_args.args[0]
    payload = fake_post.call_args.kwargs["json"]
    assert url == "https://example.com/webhook"
    assert payload == {"text": "OV Builder failed: web01 by alice — ovbuilder exited with code 2"}


def test_slack_webhook_escapes_mentions_and_links_job(monkeypatch, fake_post, no_owner):
    _env(monkeypatch, notify_webhook_url="https://example.com/webhook",
         public_base_url="https://builder.example.com/")
    job = _job(hostname="<!channel>", vm_ip="10.0.0.8")
    notify.notify_job(job)
    text = fake_post.call_args.kwargs["json"]["text"]
    assert "<!channel>" not in text
    assert "&lt;!channel&gt;" in text
    assert text.endswith(f"(10.0.0.8) https://builder.example.com/jobs/{job.id}")


def test_json_webhook_payload_has_no_secrets(monkeypatch, fake_post, no_owner):
    _env(monkeypatch, notify_webhook_url="https://example.com/webhook", notify_webhook_format="json")
    job = _job(vm_ip="10.0.0.8")
    notify.notify_job(job)
    payload = fake_post.call_args.kwargs["json"]
    assert payload["event"] == "build.succeeded"
    assert payload["job"]["job_id"] == job.id
    assert payload["job"]["duration"] == "3m07s"
    assert placeholder_value() not in str(payload)


def test_custom_text_template(monkeypatch, fake_post, no_owner):
    _env(monkeypatch, notify_webhook_url="https://example.com/webhook",
         notify_text_template="$hostname is $status after $duration ($unknown)")
    notify.notify_job(_job())
    assert fake_post.call_args.kwargs["json"]["text"] == "web01 is succeeded after 3m07s ($unknown)"


def test_runtime_settings_override_env(monkeypatch, fake_post, no_owner):
    from app.runtime_settings import RuntimeSettingsUpdate, update_runtime_settings

    _env(monkeypatch, notify_webhook_url="https://example.com/webhook-env")
    update_runtime_settings(RuntimeSettingsUpdate(notify_webhook_url="https://example.com/webhook-runtime"))
    notify.notify_job(_job())
    assert fake_post.call_args.args[0] == "https://example.com/webhook-runtime"


def test_ovbuilder_env_aliases_for_secrets(monkeypatch):
    _env(monkeypatch, ovbuilder_notify_webhook_url="https://example.com/webhook",
         ovbuilder_smtp_password=placeholder_value())
    cfg = notify.load_config()
    assert cfg.webhook_url == "https://example.com/webhook"
    assert cfg.smtp_password == placeholder_value()


def test_global_toggle_suppresses_event(monkeypatch, fake_post, no_owner):
    from app.runtime_settings import RuntimeSettingsUpdate, update_runtime_settings

    _env(monkeypatch, notify_webhook_url="https://example.com/webhook")
    update_runtime_settings(RuntimeSettingsUpdate(notify_on_success=False))
    notify.notify_job(_job(BuildStatus.succeeded))
    fake_post.assert_not_called()
    notify.notify_job(_job(BuildStatus.failed))
    fake_post.assert_called_once()


def test_non_http_webhook_is_skipped(monkeypatch, fake_post, no_owner):
    _env(monkeypatch, notify_webhook_url="file:///etc/passwd")
    notify.notify_job(_job())
    fake_post.assert_not_called()


def _smtp_env(monkeypatch, **extra: str) -> None:
    _env(
        monkeypatch,
        notify_email_enabled="true",
        smtp_host="smtp.example.com",
        smtp_user="builder@example.com",
        smtp_password=placeholder_value(),
        **extra,
    )


def test_email_to_extra_recipients_with_starttls_and_login(monkeypatch, fake_smtp, no_owner):
    _smtp_env(monkeypatch, notify_email_to="ops@example.com; ops@example.com, not-an-address")
    notify.notify_job(_job(BuildStatus.failed, error="boom"))
    (smtp,) = fake_smtp.instances
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert smtp.started_tls is True
    assert smtp.logged_in_as == "builder@example.com"
    (msg,) = smtp.sent
    assert msg["To"] == "ops@example.com"
    assert msg["From"] == "builder@example.com"
    assert msg["Subject"] == "[OV Builder] web01 failed"
    assert "Error:        boom" in msg.get_content()
    assert placeholder_value() not in msg.as_string()


def test_email_subject_cannot_inject_headers(monkeypatch, fake_smtp, no_owner):
    _smtp_env(monkeypatch, notify_email_to="ops@example.com")
    notify.notify_job(_job(hostname="web01\r\nBcc: victim@example.com"))
    (msg,) = fake_smtp.instances[0].sent
    assert msg["Bcc"] is None
    assert "\n" not in msg["Subject"]


def test_email_owner_respects_prefs():
    cfg = notify.NotifyConfig(email_enabled=True, smtp_host="smtp", smtp_from="b@example.com",
                              email_to=("ops@example.com",))
    job = _job(BuildStatus.cancelled)
    defaults = NotificationPrefs()
    assert notify.email_recipients(job, cfg, owner_email="alice@example.com", prefs=defaults) == [
        "ops@example.com"
    ]
    opted_in = NotificationPrefs(on_cancelled=True)
    assert notify.email_recipients(job, cfg, owner_email="alice@example.com", prefs=opted_in) == [
        "alice@example.com",
        "ops@example.com",
    ]
    muted = NotificationPrefs(email_enabled=False, on_cancelled=True)
    assert notify.email_recipients(job, cfg, owner_email="alice@example.com", prefs=muted) == [
        "ops@example.com"
    ]
    no_owner_cfg = notify.NotifyConfig(email_owner=False, email_to=("ops@example.com",))
    assert notify.email_recipients(job, no_owner_cfg, owner_email="alice@example.com",
                                   prefs=opted_in) == ["ops@example.com"]


async def test_email_owner_from_directory_and_prefs(job_db, monkeypatch, fake_smtp):
    _smtp_env(monkeypatch)
    await upsert_user(username="alice", ldap_role=Role.builder, email="alice@example.com")
    notify.notify_job(_job(BuildStatus.succeeded))
    assert [m["To"] for m in fake_smtp.instances[0].sent] == ["alice@example.com"]

    await set_notification_prefs("alice", NotificationPrefsUpdate(on_success=False))
    fake_smtp.instances = []
    notify.notify_job(_job(BuildStatus.succeeded))
    assert fake_smtp.instances == []


def test_smtp_ssl_skips_starttls(monkeypatch, fake_smtp, no_owner):
    _smtp_env(monkeypatch, notify_email_to="ops@example.com", smtp_ssl="true", smtp_port="465")
    notify.notify_job(_job())
    (smtp,) = fake_smtp.instances
    assert smtp.port == 465
    assert smtp.started_tls is False


def test_transport_failures_never_raise(monkeypatch, fake_smtp, no_owner):
    _smtp_env(monkeypatch, notify_email_to="ops@example.com",
              notify_webhook_url="https://example.com/webhook")
    monkeypatch.setattr("httpx.post", MagicMock(side_effect=RuntimeError("network down")))
    fake_smtp.fail_send = True
    notify.notify_job(_job())
    assert len(fake_smtp.instances) == 1


def test_webhook_failure_does_not_block_email(monkeypatch, fake_smtp, no_owner):
    _smtp_env(monkeypatch, notify_email_to="ops@example.com",
              notify_webhook_url="https://example.com/webhook")
    monkeypatch.setattr("httpx.post", MagicMock(side_effect=RuntimeError("network down")))
    notify.notify_job(_job())
    assert len(fake_smtp.instances[0].sent) == 1


def test_config_failure_never_raises(monkeypatch, fake_post):
    monkeypatch.setattr(notify, "load_config", MagicMock(side_effect=ValueError("bad config")))
    notify.notify_job(_job())
    fake_post.assert_not_called()


def test_run_build_succeeds_when_notifications_explode(job_db, monkeypatch):
    _env(monkeypatch, notify_webhook_url="https://example.com/webhook")
    monkeypatch.setattr("httpx.post", MagicMock(side_effect=RuntimeError("network down")))
    job = _job(BuildStatus.queued)
    save_job_sync(job)

    def fake_popen(cmd, **kwargs):
        proc = MagicMock()
        proc.stdout = iter(["assigned 10.0.0.8\n"])
        proc.wait.return_value = 0
        return proc

    monkeypatch.setattr("app.tasks.subprocess.Popen", fake_popen)
    run_build.apply(args=(job.id,))
    stored = get_job_sync(job.id)
    assert stored is not None
    assert stored.status == BuildStatus.succeeded


def test_my_notification_prefs_api(client):
    headers = auth_header("carol", Role.viewer)
    res = client.get("/api/auth/me/notifications", headers=headers)
    assert res.status_code == 200
    assert res.json() == NotificationPrefs().model_dump()

    res = client.put("/api/auth/me/notifications", headers=headers,
                     json={"on_success": False, "on_cancelled": True})
    assert res.status_code == 200
    assert res.json()["on_success"] is False
    assert res.json()["on_cancelled"] is True
    assert res.json()["email_enabled"] is True

    other = client.get("/api/auth/me/notifications", headers=auth_header("dave", Role.viewer))
    assert other.json() == NotificationPrefs().model_dump()


def test_my_notification_prefs_requires_auth(client):
    assert client.get("/api/auth/me/notifications").status_code == 401


def test_settings_api_never_returns_smtp_password(client):
    headers = auth_header("root", Role.admin)
    secret = placeholder_value()
    res = client.put("/api/settings", headers=headers, json={
        "notify_email_enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": 2525,
        "smtp_password": secret,
        "notify_webhook_format": "json",
    })
    assert res.status_code == 200
    body = res.json()
    assert secret not in res.text
    assert body["smtp_password_set"] is True
    assert body["notify_email_enabled"] is True
    assert body["smtp_port"] == 2525
    assert body["notify_webhook_format"] == "json"

    res = client.put("/api/settings", headers=headers, json={"notify_webhook_format": "xml"})
    assert res.status_code == 422


def test_secret_fields_are_write_only():
    from app.runtime_settings import _SECRET_KEYS, RuntimeSettingsPublic

    assert len(_SECRET_KEYS) == 3
    assert not _SECRET_KEYS & set(RuntimeSettingsPublic.model_fields)


def test_settings_api_admin_only(client):
    res = client.put("/api/settings", headers=auth_header("bob", Role.builder),
                     json={"smtp_host": "smtp.example.com"})
    assert res.status_code == 403
