"""Login, current user, and admin role-override endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from ..auth import (
    create_token,
    get_current_user,
    ldap_authenticate,
    require_admin,
    role_from_groups,
)
from ..config import get_settings
from ..database import list_users, set_role_override, upsert_user
from ..models import Role, RoleOverrideIn, Token, UserAdminOut, UserOut, effective_role

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/login", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    settings = get_settings()
    if not settings.ldap_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LDAP disabled")
    info = ldap_authenticate(settings, form.username, form.password)
    if info is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad credentials")
    ldap_role = role_from_groups(settings, info.get("groups", []))
    try:
        record = await upsert_user(
            username=info["username"],
            email=info.get("email"),
            display_name=info.get("display_name"),
            ldap_role=ldap_role,
            ldap_checked_at=datetime.now(timezone.utc),
        )
        role = effective_role(record.ldap_role, record.role_override)
    except Exception:
        logger.exception("failed to persist user %s on login", info["username"])
        role = ldap_role
    token = create_token(info["username"], role, settings)
    return Token(
        access_token=token,
        role=role,
        username=info["username"],
    )


@router.get("/me", response_model=UserOut)
async def me(user: UserOut = Depends(get_current_user)):
    return user


@router.get("/roles")
async def roles():
    """Public list of assignable roles (for the UI)."""
    return [r.value for r in Role]


@router.get("/users", response_model=list[UserAdminOut])
async def admin_list_users(_admin: UserOut = Depends(require_admin)):
    """List persisted users, LDAP roles, and local overrides."""
    return await list_users()


@router.put("/users/{username}", response_model=UserAdminOut)
async def admin_set_role_override(
    username: str,
    body: RoleOverrideIn,
    _admin: UserOut = Depends(require_admin),
):
    """Set or clear a local role override for *username*.

    ``role_override: null`` clears the override so the LDAP mapping wins
    again. The change is visible on the user's next authenticated request
    (no wait for JWT expiry). The user row is created if they have not
    signed in yet.
    """
    cleaned = username.strip()
    if not cleaned or len(cleaned) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid username")
    return await set_role_override(cleaned, body.role_override)
