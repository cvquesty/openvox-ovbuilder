"""Live vSphere inventory for the form (clusters, OS images, networks).

The web UI never exposes individual datastores — the user picks an
environment (dev/prod) and the backend maps it to the right Storage DRS
cluster behind the scenes.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from fastapi import APIRouter, Depends

from ..auth import require_builder
from ..models import UserOut
from ..vsphere_client import vsphere_session

logger = logging.getLogger(__name__)

router = APIRouter()

_FALLBACK_OS = [
    {"key": "ubuntu-24.04", "label": "Ubuntu 24.04 LTS", "default_user": "ubuntu"},
    {"key": "almalinux-10", "label": "AlmaLinux 10", "default_user": "almalinux"},
]

_FALLBACK_ENVS = [
    {"key": "dev", "label": "Development", "datastore_cluster": "YAVIN-DEV"},
    {"key": "prod", "label": "Production", "datastore_cluster": "YAVIN-PROD"},
]

_FALLBACK_CLUSTERS = ["Production Cluster", "Development Cluster"]
_FALLBACK_NETWORKS = ["VM Production", "VM Development"]


@router.get("/os-images")
async def os_images(user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
    return _FALLBACK_OS


@router.get("/environments")
async def environments(user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
    """dev/prod → datastore cluster mapping. Edit to match your site."""
    return _FALLBACK_ENVS


def _live_lists() -> tuple[List[str], List[str]]:
    """Pull compute clusters and networks from vCenter. Falls back on error."""
    from ovbuilder import vsphere as ovv

    try:
        with vsphere_session() as (si, datacenter):
            clusters = ovv.list_clusters(si, datacenter) if datacenter else []
            networks = ovv.list_networks(si, datacenter) if datacenter else []
            if clusters or networks:
                return clusters or _FALLBACK_CLUSTERS, networks or _FALLBACK_NETWORKS
    except Exception:
        logger.warning("live vSphere inventory unavailable; using fallbacks", exc_info=True)
    return _FALLBACK_CLUSTERS, _FALLBACK_NETWORKS


@router.get("/clusters")
async def clusters(user: UserOut = Depends(require_builder)) -> List[str]:
    clusters, _ = _live_lists()
    return clusters


@router.get("/networks")
async def networks(user: UserOut = Depends(require_builder)) -> List[str]:
    _, nets = _live_lists()
    return nets
