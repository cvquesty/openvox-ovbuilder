"""Login + me endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from ..auth import create_token, get_current_user, ldap_authenticate, role_from_groups
from ..config import get_settings
from ..models import Role, Token, UserOut

router = APIRouter()


@router.post("/login", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    settings = get_settings()
    if not settings.ldap_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LDAP disabled")
    info = ldap_authenticate(settings, form.username, form.password)
    if info is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad credentials")
    role = role_from_groups(settings, info.get("groups", []))
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
