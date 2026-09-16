"""Live vSphere inventory for the form (clusters, OS images, networks).

The web UI never exposes individual datastores — the user picks an
environment (dev/prod) and the backend maps it to the right Storage DRS
cluster behind the scenes.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from ..auth import require_builder
from ..config import get_settings
from ..models import UserOut

router = APIRouter()


@router.get("/os-images")
async def os_images(user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
    # Mirrors ovbuilder.config._default_golden_images keys.
    return [
        {"key": "ubuntu-24.04", "label": "Ubuntu 24.04 LTS", "default_user": "ubuntu"},
        {"key": "almalinux-10", "label": "AlmaLinux 10", "default_user": "almalinux"},
    ]


@router.get("/environments")
async def environments(user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
    """dev/prod → datastore cluster mapping. Edit to match your site."""
    return [
        {"key": "dev", "label": "Development", "datastore_cluster": "YAVIN-DEV"},
        {"key": "prod", "label": "Production", "datastore_cluster": "YAVIN-PROD"},
    ]


@router.get("/clusters")
async def clusters(user: UserOut = Depends(require_builder)) -> List[str]:
    settings = get_settings()
    # In production this would call vsphere.list_clusters(si, dc).
    # Scaffold returns the configured defaults.
    return ["Production Cluster", "Development Cluster"]


@router.get("/networks")
async def networks(user: UserOut = Depends(require_builder)) -> List[str]:
    return ["VM Production", "VM Development"]
