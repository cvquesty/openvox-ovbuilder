"""Persisted admin runtime settings (vSphere, registries, notifications).

Stored as JSON under OVBUILDER_DATA_DIR (default: ~/.local/share/ovbuilder/web)
with mode 0600. Secrets are never returned by the API — only booleans indicating
whether a secret is set.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_lock = Lock()


def _data_dir() -> Path:
    override = os.environ.get("OVBUILDER_DATA_DIR") or os.environ.get("XDG_DATA_HOME")
    if override:
        base = Path(override).expanduser()
        if os.environ.get("OVBUILDER_DATA_DIR"):
            return base
        return base / "ovbuilder" / "web"
    return Path.home() / ".local" / "share" / "ovbuilder" / "web"


def settings_path() -> Path:
    return _data_dir() / "runtime_settings.json"


WebhookFormat = Literal["slack", "json"]
_TEMPLATE_MAX = 8000


def _secret() -> Any:
    """Write-only field: never returned by the API; None on update = unchanged."""
    return Field(default="", json_schema_extra={"secret": True})


class RuntimeSettings(BaseModel):
    vsphere_server: str = ""
    vsphere_user: str = ""
    vsphere_password: str = _secret()
    vsphere_datacenter: str = ""
    vsphere_ignore_ssl: bool = True
    npm_registry: str = "https://registry.npmjs.org/"
    notify_webhook_url: str = _secret()
    notify_on_success: bool = True
    notify_on_failure: bool = True
    notify_on_cancelled: bool = True
    # Empty string / None below = inherit the env (.env) value.
    notify_webhook_format: Optional[WebhookFormat] = None
    public_base_url: str = ""
    notify_text_template: str = ""
    notify_email_subject_template: str = ""
    notify_email_body_template: str = ""
    notify_email_enabled: Optional[bool] = None
    notify_email_owner: Optional[bool] = None
    notify_email_to: str = ""
    smtp_host: str = ""
    smtp_port: Optional[int] = None
    smtp_user: str = ""
    smtp_password: str = _secret()
    smtp_starttls: Optional[bool] = None
    smtp_ssl: Optional[bool] = None
    smtp_from: str = ""


class RuntimeSettingsPublic(BaseModel):
    vsphere_server: str = ""
    vsphere_user: str = ""
    vsphere_password_set: bool = False
    vsphere_datacenter: str = ""
    vsphere_ignore_ssl: bool = True
    npm_registry: str = "https://registry.npmjs.org/"
    notify_webhook_set: bool = False
    notify_on_success: bool = True
    notify_on_failure: bool = True
    notify_on_cancelled: bool = True
    # Effective values (runtime override, else env) so the form shows reality.
    notify_webhook_format: WebhookFormat = "slack"
    public_base_url: str = ""
    notify_text_template: str = ""
    notify_email_subject_template: str = ""
    notify_email_body_template: str = ""
    notify_email_enabled: bool = False
    notify_email_owner: bool = True
    notify_email_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password_set: bool = False
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_from: str = ""


class RuntimeSettingsUpdate(BaseModel):
    vsphere_server: Optional[str] = None
    vsphere_user: Optional[str] = None
    # None = leave unchanged; "" = clear
    vsphere_password: Optional[str] = Field(default=None)
    vsphere_datacenter: Optional[str] = None
    vsphere_ignore_ssl: Optional[bool] = None
    npm_registry: Optional[str] = None
    notify_webhook_url: Optional[str] = Field(default=None)
    notify_on_success: Optional[bool] = None
    notify_on_failure: Optional[bool] = None
    notify_on_cancelled: Optional[bool] = None
    notify_webhook_format: Optional[WebhookFormat] = None
    public_base_url: Optional[str] = Field(default=None, max_length=512)
    notify_text_template: Optional[str] = Field(default=None, max_length=_TEMPLATE_MAX)
    notify_email_subject_template: Optional[str] = Field(default=None, max_length=512)
    notify_email_body_template: Optional[str] = Field(default=None, max_length=_TEMPLATE_MAX)
    notify_email_enabled: Optional[bool] = None
    notify_email_owner: Optional[bool] = None
    notify_email_to: Optional[str] = Field(default=None, max_length=2048)
    smtp_host: Optional[str] = Field(default=None, max_length=255)
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_user: Optional[str] = Field(default=None, max_length=255)
    # None = leave unchanged; "" = clear
    smtp_password: Optional[str] = Field(default=None)
    smtp_starttls: Optional[bool] = None
    smtp_ssl: Optional[bool] = None
    smtp_from: Optional[str] = Field(default=None, max_length=255)


_SECRET_KEYS = frozenset(
    name
    for name, field in RuntimeSettings.model_fields.items()
    if (field.json_schema_extra or {}).get("secret")
)


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass


def load_runtime_settings() -> RuntimeSettings:
    path = settings_path()
    if not path.exists():
        return RuntimeSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return RuntimeSettings()
        return RuntimeSettings(**{k: v for k, v in raw.items() if k in RuntimeSettings.model_fields})
    except Exception:
        logger.exception("Failed to load runtime settings from %s", path)
        return RuntimeSettings()


def save_runtime_settings(cfg: RuntimeSettings) -> None:
    path = settings_path()
    _ensure_dir(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(cfg.model_dump_json(indent=2) + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def effective_value(cfg: Optional[RuntimeSettings], env: Any, name: str) -> Any:
    """Runtime override when set (not None / ""), else the env Settings value."""
    value = getattr(cfg, name, None) if cfg is not None else None
    if value is None or value == "":
        return getattr(env, name)
    return value


def to_public(cfg: RuntimeSettings) -> RuntimeSettingsPublic:
    from .config import get_settings

    env = get_settings()

    def pick(name: str):
        return effective_value(cfg, env, name)

    fmt = pick("notify_webhook_format")
    return RuntimeSettingsPublic(
        notify_webhook_format=fmt if fmt in ("slack", "json") else "slack",
        public_base_url=pick("public_base_url"),
        notify_text_template=pick("notify_text_template"),
        notify_email_subject_template=pick("notify_email_subject_template"),
        notify_email_body_template=pick("notify_email_body_template"),
        notify_email_enabled=bool(pick("notify_email_enabled")),
        notify_email_owner=bool(pick("notify_email_owner")),
        notify_email_to=pick("notify_email_to"),
        smtp_host=pick("smtp_host"),
        smtp_port=int(pick("smtp_port")),
        smtp_user=pick("smtp_user"),
        smtp_password_set=bool(cfg.smtp_password or env.smtp_password),
        smtp_starttls=bool(pick("smtp_starttls")),
        smtp_ssl=bool(pick("smtp_ssl")),
        smtp_from=pick("smtp_from"),
        vsphere_server=cfg.vsphere_server,
        vsphere_user=cfg.vsphere_user,
        vsphere_password_set=bool(cfg.vsphere_password),
        vsphere_datacenter=cfg.vsphere_datacenter,
        vsphere_ignore_ssl=cfg.vsphere_ignore_ssl,
        npm_registry=cfg.npm_registry or "https://registry.npmjs.org/",
        notify_webhook_set=bool(cfg.notify_webhook_url),
        notify_on_success=cfg.notify_on_success,
        notify_on_failure=cfg.notify_on_failure,
        notify_on_cancelled=cfg.notify_on_cancelled,
    )


def update_runtime_settings(patch: RuntimeSettingsUpdate) -> RuntimeSettings:
    with _lock:
        current = load_runtime_settings()
        data: Dict[str, Any] = current.model_dump()
        payload = patch.model_dump(exclude_unset=True)
        for key, value in payload.items():
            if key in _SECRET_KEYS:
                if value is None:
                    continue
                data[key] = value
            elif value is not None:
                data[key] = value
        updated = RuntimeSettings(**data)
        save_runtime_settings(updated)
        return updated
