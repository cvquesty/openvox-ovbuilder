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
_FALLBACK_ENV = [
    {"key": "dev", "label": "Development", "datastore_cluster": "YAVIN-DEV"},
    {"key": "prod", "label": "Production", "datastore_cluster": "YAVIN-PROD"},
]
_FALLBACK_CLUSTERS = ["Production Cluster", "Development Cluster"]
_FALLBACK_NETWORKS = ["VM Production", "VM Development"]


def _names(items) -> List[str]:
    out: List[str] = []
    for item in items or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("label") or item.get("key")
            if name:
                out.append(str(name))
        else:
            name = getattr(item, "name", None)
            if name:
                out.append(str(name))
    return out


@router.get("/os-images")
async def os_images(user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
    try:
        from ovbuilder.config import get_config_manager

        cfg = get_config_manager().load_config()
        images = getattr(cfg, "golden_images", None) or getattr(cfg, "os_images", None)
        if isinstance(images, dict) and images:
            rows: List[Dict[str, str]] = []
            for key, meta in images.items():
                if isinstance(meta, dict):
                    rows.append({
                        "key": str(key),
                        "label": str(meta.get("label") or key),
                        "default_user": str(meta.get("default_user") or meta.get("user") or ""),
                    })
                else:
                    rows.append({"key": str(key), "label": str(key), "default_user": ""})
            if rows:
                return rows
    except Exception:
        logger.debug("os-images: CLI config unavailable, using fallback", exc_info=True)
    return list(_FALLBACK_OS)


@router.get("/environments")
async def environments(user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
    return list(_FALLBACK_ENV)


@router.get("/clusters")
async def clusters(user: UserOut = Depends(require_builder)) -> List[str]:
    try:
        from ovbuilder import vsphere

        with vsphere_session() as (si, datacenter):
            raw = None
            if hasattr(vsphere, "list_clusters"):
                raw = vsphere.list_clusters(si, datacenter or None)
            elif hasattr(vsphere, "list_compute_clusters"):
                raw = vsphere.list_compute_clusters(si, datacenter or None)
            names = _names(raw)
            if names:
                return names
    except Exception:
        logger.warning("live cluster inventory failed; using fallback", exc_info=True)
    return list(_FALLBACK_CLUSTERS)


@router.get("/networks")
async def networks(user: UserOut = Depends(require_builder)) -> List[str]:
    try:
        from ovbuilder import vsphere

        with vsphere_session() as (si, datacenter):
            raw = None
            if hasattr(vsphere, "list_networks"):
                raw = vsphere.list_networks(si, datacenter or None)
            elif hasattr(vsphere, "list_portgroups"):
                raw = vsphere.list_portgroups(si, datacenter or None)
            names = _names(raw)
            if names:
                return names
    except Exception:
        logger.warning("live network inventory failed; using fallback", exc_info=True)
    return list(_FALLBACK_NETWORKS)
