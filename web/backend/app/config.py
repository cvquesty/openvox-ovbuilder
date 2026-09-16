"""Settings for the OV Builder web app.

Loaded from environment variables (and optionally a .env file). Mirrors the
keys your openvox-gui LDAP config uses so the same directory works for both.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ----------------------------------------------------------------
    app_name: str = "OpenVox OV Builder"
    debug: bool = False
    secret_key: str = Field(default="change-me-in-production", min_length=16)
    access_token_expire_minutes: int = 60 * 8

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
    ldap_ssl_verify: bool = False
    ldap_connection_timeout: int = 10

    # --- Celery / Redis -----------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # --- Postgres (build history + audit) -----------------------------------
    database_url: str = "postgresql+asyncpg://ovbuilder:ovbuilder@postgres:5432/ovbuilder"

    # --- ovbuilder CLI integration -----------------------------------------
    # The web backend shells out to `ovbuilder build --yes ...`.
    ovbuilder_binary: str = "ovbuilder"
    # Directory the CLI uses for its own config/secrets (golden password, etc.).
    ovbuilder_home: str = "/opt/ovbuilder"
    # How many concurrent Celery workers may run builds at once.
    max_concurrent_builds: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
