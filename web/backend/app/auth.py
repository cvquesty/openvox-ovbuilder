"""LDAP authentication (bind-search-bind) + JWT sessions.

Reuses the same pattern as openvox-gui's auth_ldap.py, but simplified for the
builder's three roles: admin, builder, viewer. Group membership decides the
role on first login; admins can override locally later.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext

from .config import Settings, get_settings
from .models import Role, UserOut

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
    return Role(settings.ldap_default_role)


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
    return UserOut(username=payload["sub"], role=Role(payload.get("role", "viewer")))


def require_role(*roles: Role):
    async def _dep(user: UserOut = Depends(get_current_user)) -> UserOut:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user
    return _dep


require_builder = require_role(Role.admin, Role.builder)
require_admin = require_role(Role.admin)
