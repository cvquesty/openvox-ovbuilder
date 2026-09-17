"""Admin-only runtime configuration (vSphere + package registries)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_admin
from ..models import UserOut
from ..runtime_settings import (
    RuntimeSettingsPublic,
    RuntimeSettingsUpdate,
    load_runtime_settings,
    to_public,
    update_runtime_settings,
)

router = APIRouter()


@router.get("", response_model=RuntimeSettingsPublic)
async def get_settings(user: UserOut = Depends(require_admin)) -> RuntimeSettingsPublic:
    return to_public(load_runtime_settings())


@router.put("", response_model=RuntimeSettingsPublic)
async def put_settings(
    body: RuntimeSettingsUpdate,
    user: UserOut = Depends(require_admin),
) -> RuntimeSettingsPublic:
    return to_public(update_runtime_settings(body))
