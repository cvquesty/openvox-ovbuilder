"""Settings for the OV Builder web app.

Loaded from environment variables (and optionally a .env file). Mirrors the
keys your openvox-gui LDAP config uses so the same directory works for both.

Bare-metal defaults: Redis and Postgres run as local system services, not in
containers. Override via .env if you ever containerize.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Literal, Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Historic example values that must never be accepted as a real JWT secret.
_INSECURE_SECRET_KEYS = frozenset(
    {
        "",
        "change-me-in-production",
        "change-me-to-a-long-random-string",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ----------------------------------------------------------------
    app_name: str = "OpenVox OV Builder"
    debug: bool = False
    # No insecure default. Missing/example keys fail fast unless DEBUG=true,
    # which is an explicit opt-in to an ephemeral (non-persistent) key.
    secret_key: str = Field(default="")
    access_token_expire_minutes: int = 60 * 8
    # How long a directory-mapped role may be reused before the next LDAP
    # group lookup. 0 = re-check on every authenticated request. Local
    # overrides are always applied immediately and never wait on this TTL.
    role_cache_ttl_seconds: int = 60

    # --- CORS (React dev server + production origin) ------------------------
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )

    # --- LDAP (OpenLDAP, bind-search-bind) ----------------------------------
    ldap_enabled: bool = True
    ldap_server_url: str = "ldap://ldap.example.com:389"
    ldap_bind_dn: str = "cn=ovbuilder-svc,ou=services,dc=example,dc=com"
    ldap_bind_password: str = ""
    ldap_user_base_dn: str = "ou=people,dc=example,dc=com"
    ldap_user_search_filter: str = "(uid={username})"
    ldap_user_attr_username: str = "uid"
    ldap_user_attr_email: str = "mail"
    ldap_user_attr_display_name: str = "cn"
    ldap_group_base_dn: str = "ou=groups,dc=example,dc=com"
    ldap_group_search_filter: str = "(objectClass=groupOfNames)"
    ldap_group_member_attr: str = "member"
    ldap_group_attr_name: str = "cn"
    # Three roles for the builder: admin > builder > viewer
    ldap_admin_group: str = "ovbuilder-admins"
    ldap_builder_group: str = "ovbuilder-builders"
    ldap_viewer_group: str = "ovbuilder-viewers"
    ldap_default_role: str = "viewer"
    ldap_use_ssl: bool = False
    ldap_use_starttls: bool = False
    # Default to verifying certs; set false only as an explicit lab opt-in.
    ldap_ssl_verify: bool = True
    # PEM bundle for a private LDAP CA. Used when ldap_ssl_verify is true.
    ldap_ca_certs_file: Optional[str] = None
    ldap_connection_timeout: int = 10

    @model_validator(mode="after")
    def _validate_secret_key(self) -> "Settings":
        key = (self.secret_key or "").strip()
        if key in _INSECURE_SECRET_KEYS:
            if self.debug:
                logger.warning(
                    "SECRET_KEY is unset or using the example value; "
                    "generating an ephemeral key because DEBUG=true. "
                    "Tokens will not survive process restart. "
                    "Set SECRET_KEY in .env for any shared or persistent deploy."
                )
                self.secret_key = secrets.token_urlsafe(48)
                return self
            raise ValueError(
                "SECRET_KEY is unset or using an insecure default. "
                "Set SECRET_KEY to at least 32 random characters "
                '(python -c "import secrets; print(secrets.token_urlsafe(48))"). '
                "For local development only, set DEBUG=true to allow an "
                "ephemeral key."
            )
        if len(key) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return self

    # --- Celery / Redis (local system services, not containers) ------------
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"

    # --- Postgres (local system service) ------------------------------------
    # No role password in the committed default. Set one in gitignored .env.
    database_url: str = "postgresql+asyncpg://ovbuilder@127.0.0.1:5432/ovbuilder"

    # --- ovbuilder CLI integration -----------------------------------------
    ovbuilder_binary: str = "ovbuilder"
    ovbuilder_home: str = "/opt/ovbuilder"
    max_concurrent_builds: int = 4
    build_time_limit_seconds: int = 60 * 60
    # On cancel, the build's process group gets SIGTERM (Terraform stops
    # gracefully and saves state), then SIGKILL after this many seconds.
    build_cancel_grace_seconds: int = 60
    max_queued_per_user: int = 8
    # Host-wide backpressure: submit is 429 when queued+running reaches
    # max_concurrent_builds + max_queue_depth.
    max_queue_depth: int = 32

    # --- SQLAlchemy pools (Postgres only; sqlite tests use StaticPool) ------
    db_pool_size: int = 5
    db_max_overflow: int = 10
    worker_db_pool_size: int = 2
    worker_db_max_overflow: int = 2

    # --- vSphere (web worker / inventory / lifecycle) ----------------------
    # Used by vsphere_client.py. Empty strings fall back to the CLI config.
    vsphere_server: str = ""
    vsphere_user: str = ""
    vsphere_password: str = ""
    vsphere_datacenter: str = ""
    vsphere_ignore_ssl: bool = True
    # Socket timeout for vCenter connections opened by the web app, so an
    # unreachable vCenter fails fast instead of tying up a request thread.
    vsphere_connect_timeout_seconds: int = 15

    # --- Live inventory cache (per process) ---------------------------------
    # Fresh window for cached inventory lists. 0 = always query vCenter.
    inventory_cache_ttl_seconds: int = 300
    # How long the last good list may be served (flagged stale) while
    # vCenter is unreachable. 0 = never serve stale data.
    inventory_stale_ttl_seconds: int = 3600
    # After a failed vCenter call, skip new attempts for this long.
    inventory_error_backoff_seconds: int = 15

    # --- VM lifecycle / reclaim policy --------------------------------------
    # Only VMs created by a succeeded build are ever leased or reclaimed.
    # Default lease from build completion; 0 = no expiry unless one is set.
    vm_lease_days: int = 0
    # Furthest a builder may push their own lease from now (admins: no cap).
    vm_lease_max_days: int = 90
    # Reclaim a VM seen powered off for this many days; 0 = never.
    vm_idle_days: int = 0
    # What reclaim does to an expired or idle VM: power_off | destroy.
    vm_reclaim_action: Literal["power_off", "destroy"] = "power_off"
    # The scheduled reclaim (systemd timer) acts only when true; previews
    # from the admin UI/API work either way.
    vm_reclaim_enabled: bool = False
    # Builders may destroy VMs their own succeeded builds created.
    vm_builder_can_destroy: bool = True


    # --- Notifications (all channels off until configured) ------------------
    # Admin Settings (runtime_settings.json) override these per field.
    # Secrets: .env / env only (OVBUILDER_* aliases like the vSphere password).
    notify_webhook_url: str = Field(
        default="",
        validation_alias=AliasChoices("notify_webhook_url", "ovbuilder_notify_webhook_url"),
    )
    # "slack" posts {"text": ...}; "json" posts the full event plus "text".
    notify_webhook_format: str = "slack"
    notify_on_success: bool = True
    notify_on_failure: bool = True
    notify_on_cancelled: bool = True
    # Prefix for $job_url in templates, e.g. https://builder.example.com
    public_base_url: str = ""
    # Empty = built-in default templates. string.Template syntax ($hostname).
    notify_text_template: str = ""
    notify_email_subject_template: str = ""
    notify_email_body_template: str = ""

    # Email requires NOTIFY_EMAIL_ENABLED=true and SMTP_HOST.
    notify_email_enabled: bool = False
    # Send to the requester's directory email (subject to their preferences).
    notify_email_owner: bool = True
    # Comma-separated extra recipients (e.g. an ops list); gets every event
    # allowed by the global on_* toggles.
    notify_email_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = Field(
        default="",
        validation_alias=AliasChoices("smtp_password", "ovbuilder_smtp_password"),
    )
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_from: str = ""
    smtp_timeout_seconds: float = 10.0
    notify_webhook_timeout_seconds: float = 8.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
