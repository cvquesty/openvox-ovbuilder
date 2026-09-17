"""LDAP authentication (bind-search-bind) + JWT sessions.

Reuses the same pattern as openvox-gui's auth_ldap.py, but simplified for the
builder's three roles: admin, builder, viewer.

Effective role on each request
------------------------------
``local override`` (Postgres) beats the last LDAP group mapping. The JWT
``role`` claim is a hint used only when we have no row yet and LDAP is
unreachable. Directory changes are picked up on the next request after
``ROLE_CACHE_TTL_SECONDS`` (default 60s), not when the access token expires.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext

from .config import Settings, get_settings
from .database import get_user, upsert_user
from .models import Role, UserAdminOut, UserOut, coerce_role, effective_role

logger = logging.getLogger(__name__)

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# LDAP
# ---------------------------------------------------------------------------

def _ldap_server(settings: Settings):
    import ssl as ssl_mod
    from ldap3 import Server, Tls

    use_ssl = settings.ldap_use_ssl or settings.ldap_server_url.lower().startswith("ldaps://")
    tls_kwargs: Dict[str, Any] = {}
    if use_ssl or settings.ldap_use_starttls:
        tls_kwargs["version"] = ssl_mod.PROTOCOL_TLSv1_2
        tls_kwargs["ciphers"] = "ALL:!aNULL:!eNULL:!LOW:!EXP:!RC4:!MD5"
        tls_kwargs["validate"] = (
            ssl_mod.CERT_REQUIRED if settings.ldap_ssl_verify else ssl_mod.CERT_NONE
        )
    tls = Tls(**tls_kwargs) if tls_kwargs else None
    return Server(
        settings.ldap_server_url,
        use_ssl=use_ssl,
        tls=tls,
        connect_timeout=settings.ldap_connection_timeout,
    )


def ldap_authenticate(settings: Settings, username: str, password: str) -> Optional[Dict[str, Any]]:
    """Bind-search-bind. Returns user info dict or None."""
    import ldap3
    from ldap3 import SUBTREE

    server = _ldap_server(settings)
    try:
        svc = ldap3.Connection(
            server,
            user=settings.ldap_bind_dn,
            password=settings.ldap_bind_password,
            auto_bind=True,
            raise_exceptions=False,
            receive_timeout=settings.ldap_connection_timeout,
        )
        if not svc.bound:
            logger.error("LDAP service bind failed: %s", svc.result)
            return None

        filt = settings.ldap_user_search_filter.replace(
            "{username}", ldap3.utils.conv.escape_filter_chars(username)
        )
        svc.search(
            settings.ldap_user_base_dn,
            filt,
            search_scope=SUBTREE,
            attributes=[
                settings.ldap_user_attr_username,
                settings.ldap_user_attr_email or "mail",
                settings.ldap_user_attr_display_name or "cn",
            ],
        )
        if not svc.entries:
            svc.unbind()
            return None

        entry = svc.entries[0]
        user_dn = str(entry.entry_dn)

        user_conn = ldap3.Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
            raise_exceptions=False,
            receive_timeout=settings.ldap_connection_timeout,
        )
        if not user_conn.bound:
            svc.unbind()
            return None

        info = {
            "dn": user_dn,
            "username": username,
            "email": str(getattr(entry, settings.ldap_user_attr_email or "mail", "")) or None,
            "display_name": str(
                getattr(entry, settings.ldap_user_attr_display_name or "cn", username)
            ),
            "groups": _groups_for(svc, settings, user_dn, username),
        }
        user_conn.unbind()
        svc.unbind()
        return info
    except Exception:
        logger.exception("LDAP auth error for %s", username)
        return None


def _groups_for(conn, settings: Settings, user_dn: str, username: str) -> List[str]:
    import ldap3
    from ldap3 import SUBTREE

    if not settings.ldap_group_base_dn:
        return []
    member_attr = settings.ldap_group_member_attr or "member"
    esc_dn = ldap3.utils.conv.escape_filter_chars(user_dn)
    esc_user = ldap3.utils.conv.escape_filter_chars(username)
    filt = (
        f"(&{settings.ldap_group_search_filter}"
        f"(|({member_attr}={esc_dn})({member_attr}={esc_user})"
        f"(memberUid={esc_user})))"
    )
    try:
        conn.search(
            settings.ldap_group_base_dn,
            filt,
            search_scope=SUBTREE,
            attributes=[settings.ldap_group_attr_name or "cn"],
        )
        return [
            str(getattr(e, settings.ldap_group_attr_name or "cn", ""))
            for e in conn.entries
            if getattr(e, settings.ldap_group_attr_name or "cn", "")
        ]
    except Exception:
        logger.warning("group lookup failed for %s", username)
        return []


def role_from_groups(settings: Settings, groups: List[str]) -> Role:
    lower = {g.lower() for g in groups}
    if settings.ldap_admin_group.lower() in lower:
        return Role.admin
    if settings.ldap_builder_group.lower() in lower:
        return Role.builder
    if settings.ldap_viewer_group.lower() in lower:
        return Role.viewer
    return coerce_role(settings.ldap_default_role)


class LdapUnavailable(Exception):
    """Directory could not be reached; callers should keep the cached role."""


def ldap_lookup_user(settings: Settings, username: str) -> Optional[Dict[str, Any]]:
    """Service-bind lookup (no user password).

    Returns a user-info dict when the entry exists, ``None`` when it does not.
    Raises :class:`LdapUnavailable` on bind/connect/search failures so we do
    not treat an outage as "user vanished".
    """
    import ldap3
    from ldap3 import SUBTREE

    server = _ldap_server(settings)
    svc = None
    try:
        svc = ldap3.Connection(
            server,
            user=settings.ldap_bind_dn,
            password=settings.ldap_bind_password,
            auto_bind=True,
            raise_exceptions=False,
            receive_timeout=settings.ldap_connection_timeout,
        )
        if not svc.bound:
            logger.error("LDAP service bind failed during role refresh: %s", svc.result)
            raise LdapUnavailable("LDAP service bind failed")

        filt = settings.ldap_user_search_filter.replace(
            "{username}", ldap3.utils.conv.escape_filter_chars(username)
        )
        svc.search(
            settings.ldap_user_base_dn,
            filt,
            search_scope=SUBTREE,
            attributes=[
                settings.ldap_user_attr_username,
                settings.ldap_user_attr_email or "mail",
                settings.ldap_user_attr_display_name or "cn",
            ],
        )
        if not svc.entries:
            return None

        entry = svc.entries[0]
        user_dn = str(entry.entry_dn)
        return {
            "dn": user_dn,
            "username": username,
            "email": str(getattr(entry, settings.ldap_user_attr_email or "mail", "")) or None,
            "display_name": str(
                getattr(entry, settings.ldap_user_attr_display_name or "cn", username)
            ),
            "groups": _groups_for(svc, settings, user_dn, username),
        }
    except LdapUnavailable:
        raise
    except Exception as exc:
        logger.exception("LDAP role lookup failed for %s", username)
        raise LdapUnavailable(str(exc)) from exc
    finally:
        if svc is not None:
            try:
                svc.unbind()
            except Exception:
                pass


def _cache_is_fresh(record: UserAdminOut, settings: Settings, now: datetime) -> bool:
    ttl = max(0, int(settings.role_cache_ttl_seconds))
    if record.ldap_checked_at is None:
        return False
    checked = record.ldap_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (now - checked) < timedelta(seconds=ttl)


async def resolve_user_role(
    username: str,
    settings: Settings,
    *,
    token_role: Optional[Role] = None,
    force_refresh: bool = False,
) -> UserAdminOut:
    """Load the user row and refresh LDAP mapping when the cache is stale.

    Override, when set, is always the effective role. A confirmed empty /
    missing LDAP entry (not an outage) stores the configured default role.
    """
    now = datetime.now(timezone.utc)
    record = await get_user(username)
    should_refresh = force_refresh or record is None or not _cache_is_fresh(record, settings, now)

    if should_refresh:
        info: Optional[Dict[str, Any]]
        lookup_failed = False
        try:
            info = await asyncio.to_thread(ldap_lookup_user, settings, username)
        except LdapUnavailable:
            info = None
            lookup_failed = True

        if info is not None:
            record = await upsert_user(
                username=username,
                email=info.get("email"),
                display_name=info.get("display_name"),
                ldap_role=role_from_groups(settings, info.get("groups") or []),
                ldap_checked_at=now,
            )
        elif not lookup_failed:
            # User is gone or has no directory entry — drop to default unless
            # an override is already stored (upsert keeps it).
            record = await upsert_user(
                username=username,
                ldap_role=coerce_role(settings.ldap_default_role),
                ldap_checked_at=now,
            )
        elif record is None and token_role is not None:
            # First request, directory down: persist the JWT hint so RBAC
            # still works, but leave ldap_checked_at empty so we retry.
            record = await upsert_user(
                username=username,
                ldap_role=token_role,
                ldap_checked_at=None,
            )

    if record is None:
        fallback = token_role or coerce_role(settings.ldap_default_role)
        return UserAdminOut(
            username=username,
            ldap_role=fallback,
            role_override=None,
            role=fallback,
        )
    return record


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_token(username: str, role: Role, settings: Settings) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    return jwt.encode(
        {"sub": username, "role": role.value, "exp": expire},
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_token(token: str, settings: Settings) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# FastAPI deps
# ---------------------------------------------------------------------------

async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserOut:
    settings = get_settings()
    payload = decode_token(token, settings)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    username = str(payload["sub"])
    token_role = coerce_role(payload.get("role"), Role.viewer)
    try:
        record = await resolve_user_role(username, settings, token_role=token_role)
    except Exception:
        logger.exception("role resolve failed for %s; using token role (degraded)", username)
        return UserOut(username=username, role=token_role)
    return UserOut(
        username=record.username,
        role=effective_role(record.ldap_role, record.role_override),
        display_name=record.display_name,
        email=record.email,
    )


def require_role(*roles: Role):
    async def _dep(user: UserOut = Depends(get_current_user)) -> UserOut:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user
    return _dep


require_builder = require_role(Role.admin, Role.builder)
require_admin = require_role(Role.admin)
