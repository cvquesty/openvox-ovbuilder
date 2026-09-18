"""Live vSphere inventory for the build form.

OS images are live ``ovbuilder-*`` templates discovered across every
datacenter (config goldens are the vCenter-down fallback). Environment →
datastore-cluster mapping comes from ovbuilder config. Clusters, networks,
datacenters, and datastore clusters are listed live via ``ovbuilder.vsphere``.

Live endpoints return HTTP 502 with a clear message when vCenter is
unreachable or credentials are missing — they never invent fake names.
OS images are the exception: a failed scan falls back to configured
``ovbuilder-*`` goldens so the form still renders.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from ovbuilder import vsphere
from ovbuilder.goldens import (
    discover_goldens,
    public_os_rows,
    public_os_rows_from_config,
)
from ovbuilder.placement import (
    default_environments,
    environments_as_dicts,
)

from ..auth import require_builder
from ..models import UserOut
from ..vsphere_client import vsphere_session

logger = logging.getLogger(__name__)
router = APIRouter()

# Last-resort OS rows when CLI config cannot be loaded (matches CLI defaults).
_FALLBACK_OS = [
    {
        "key": "ubuntu-24.04",
        "label": "Ubuntu 24.04 LTS",
        "default_user": "ubuntu",
        "name": "ovbuilder-ubuntu-24.04",
    },
    {
        "key": "almalinux-10",
        "label": "AlmaLinux 10",
        "default_user": "almalinux",
        "name": "ovbuilder-almalinux-10",
    },
]


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


def _public_error(exc: Exception) -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    return msg


def _resolve_datacenter(si, configured: str) -> str:
    if configured:
        return configured
    dcs = vsphere.list_datacenters(si)
    if len(dcs) == 1:
        return dcs[0]
    if not dcs:
        raise RuntimeError("No datacenters visible in vCenter")
    raise RuntimeError(
        "Multiple datacenters found; set VSPHERE_DATACENTER or "
        "configure vsphere_datacenter in Settings / config.yaml"
    )


def _list_live(list_fn: Callable, *, needs_dc: bool = True) -> List[str]:
    try:
        with vsphere_session() as (si, datacenter):
            if needs_dc:
                dc = _resolve_datacenter(si, datacenter)
                raw = list_fn(si, dc)
            else:
                raw = list_fn(si)
            return _names(raw)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("live vSphere inventory failed", exc_info=True)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            _public_error(exc),
        ) from exc


@router.get("/os-images")
async def os_images(_user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
    """Live ovbuilder-* templates (name + label). Source DC is not returned."""
    try:
        with vsphere_session() as (si, _datacenter):
            goldens = discover_goldens(si)
        return public_os_rows(goldens)
    except HTTPException:
        raise
    except Exception:
        logger.debug(
            "os-images: live inventory unavailable, using config fallback",
            exc_info=True,
        )
    try:
        rows = public_os_rows_from_config()
        if rows:
            return rows
    except Exception:
        logger.debug("os-images: CLI config unavailable, using fallback", exc_info=True)
    return list(_FALLBACK_OS)


@router.get("/environments")
async def environments(_user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
    try:
        rows = environments_as_dicts()
        if rows:
            return rows
    except Exception:
        logger.warning("environment mapping unavailable; using built-in defaults", exc_info=True)

    return [
        {
            "key": key,
            "label": profile.label or key,
            "datastore_cluster": profile.datastore_cluster or "",
            "cluster": profile.cluster or "",
        }
        for key, profile in default_environments().items()
    ]


@router.get("/datacenters")
async def datacenters(_user: UserOut = Depends(require_builder)) -> List[str]:
    return _list_live(vsphere.list_datacenters, needs_dc=False)


@router.get("/clusters")
async def clusters(_user: UserOut = Depends(require_builder)) -> List[str]:
    return _list_live(vsphere.list_clusters)


@router.get("/networks")
async def networks(_user: UserOut = Depends(require_builder)) -> List[str]:
    return _list_live(vsphere.list_networks)


@router.get("/datastore-clusters")
async def datastore_clusters(_user: UserOut = Depends(require_builder)) -> List[str]:
    return _list_live(vsphere.list_datastore_clusters)


@router.get("/datastores")
async def datastores(_user: UserOut = Depends(require_builder)) -> List[str]:
    """Live datastore names. The build form does not expose these as a picker."""
    return _list_live(vsphere.list_datastores)
