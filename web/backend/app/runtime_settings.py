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
from typing import Any, Dict, Optional

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


class RuntimeSettings(BaseModel):
    vsphere_server: str = ""
    vsphere_user: str = ""
    vsphere_password: str = ""
    vsphere_datacenter: str = ""
    vsphere_ignore_ssl: bool = True
    npm_registry: str = "https://registry.npmjs.org/"
    notify_webhook_url: str = ""
    notify_on_success: bool = True
    notify_on_failure: bool = True
    notify_on_cancelled: bool = True


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


_SECRET_KEYS = frozenset({"vsphere_password", "notify_webhook_url"})


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


def to_public(cfg: RuntimeSettings) -> RuntimeSettingsPublic:
    return RuntimeSettingsPublic(
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
