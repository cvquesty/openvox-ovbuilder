"""Live vSphere inventory for the build form.

OS images are live ``ovbuilder-*`` templates discovered across every
datacenter (config goldens are the vCenter-down fallback). Environment →
datastore-cluster mapping comes from ovbuilder config. Clusters (ClusterComputeResource only), standalone hosts, networks,
datacenters, and datastore clusters are listed live via ``ovbuilder.vsphere``.

Live lists are cached per process (see ``app.inventory_cache``) so the build
form does not open a vCenter session per dropdown on every page load. Every
response carries ``X-Inventory-Source`` (``live``, ``cache``, ``stale`` or
``fallback``) and ``X-Inventory-Age`` (seconds). Pass ``?refresh=true`` to
bypass the fresh cache.

When vCenter is unreachable the last good list is served as ``stale``. With
no usable cached list, live endpoints return HTTP 502 with a clear message
(and ``Retry-After`` during the error backoff) — they never invent fake
names. OS images are the exception: with nothing cached, a failed scan falls
back to configured ``ovbuilder-*`` goldens so the form still renders.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
from ..config import get_settings
from ..inventory_cache import CacheResult, InventoryCache, InventoryUnavailable
from ..models import UserOut
from ..vsphere_client import session_identity, vsphere_session

logger = logging.getLogger(__name__)
router = APIRouter()

_cache = InventoryCache()

RefreshParam = Query(False, description="Bypass the fresh cache and query vCenter now")

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


def _set_source(response: Response, source: str, age: float = 0.0) -> None:
    response.headers["X-Inventory-Source"] = source
    response.headers["X-Inventory-Age"] = str(int(age))


def _cached(name: str, loader: Callable[[], Any], *, refresh: bool) -> CacheResult:
    """Load ``name`` through the shared cache. Raises InventoryUnavailable."""
    settings = get_settings()

    def _load() -> Any:
        try:
            return loader()
        except Exception:
            logger.warning("live vSphere inventory failed: %s", name, exc_info=True)
            raise

    result = _cache.get(
        (name, *session_identity(settings)),
        _load,
        ttl=max(0, settings.inventory_cache_ttl_seconds),
        stale_ttl=max(0, settings.inventory_stale_ttl_seconds),
        error_backoff=max(0, settings.inventory_error_backoff_seconds),
        refresh=refresh,
    )
    if result.source == "stale":
        logger.info(
            "serving stale vSphere inventory %s (age %ds)", name, int(result.age)
        )
    return result


def _list_live(
    name: str,
    list_fn: Callable,
    response: Response,
    *,
    refresh: bool,
    needs_dc: bool = True,
) -> List[str]:
    def _load() -> List[str]:
        with vsphere_session() as (si, datacenter):
            if needs_dc:
                return _names(list_fn(si, _resolve_datacenter(si, datacenter)))
            return _names(list_fn(si))

    try:
        result = _cached(name, _load, refresh=refresh)
    except InventoryUnavailable as exc:
        headers = (
            {"Retry-After": str(math.ceil(exc.retry_after))} if exc.retry_after else None
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            _public_error(exc),
            headers=headers,
        ) from exc
    _set_source(response, result.source, result.age)
    return list(result.value)


# Endpoints are sync on purpose: pyVmomi blocks, so FastAPI runs them in its
# threadpool instead of stalling the event loop while vCenter responds.


@router.get("/os-images")
def os_images(
    response: Response,
    refresh: bool = RefreshParam,
    _user: UserOut = Depends(require_builder),
) -> List[Dict[str, str]]:
    """Live ovbuilder-* templates (name + label). Source DC is not returned."""

    def _load() -> List[Dict[str, str]]:
        with vsphere_session() as (si, _datacenter):
            return public_os_rows(discover_goldens(si))

    try:
        result = _cached("os-images", _load, refresh=refresh)
        _set_source(response, result.source, result.age)
        return [dict(row) for row in result.value]
    except InventoryUnavailable:
        logger.debug("os-images: live inventory unavailable, using config fallback")
    _set_source(response, "fallback")
    try:
        rows = public_os_rows_from_config()
        if rows:
            return rows
    except Exception:
        logger.debug("os-images: CLI config unavailable, using fallback", exc_info=True)
    return [dict(row) for row in _FALLBACK_OS]


@router.get("/environments")
def environments(_user: UserOut = Depends(require_builder)) -> List[Dict[str, str]]:
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
def datacenters(
    response: Response,
    refresh: bool = RefreshParam,
    _user: UserOut = Depends(require_builder),
) -> List[str]:
    return _list_live(
        "datacenters", vsphere.list_datacenters, response, refresh=refresh, needs_dc=False
    )


@router.get("/clusters")
def clusters(
    response: Response,
    refresh: bool = RefreshParam,
    _user: UserOut = Depends(require_builder),
) -> List[str]:
    """Real vSphere compute clusters only (never standalone ESXi hosts)."""
    return _list_live("clusters", vsphere.list_clusters, response, refresh=refresh)


@router.get("/hosts")
def hosts(
    response: Response,
    refresh: bool = RefreshParam,
    _user: UserOut = Depends(require_builder),
) -> List[str]:
    """Standalone ESXi hosts (not members of a ClusterComputeResource)."""
    return _list_live("hosts", vsphere.list_standalone_hosts, response, refresh=refresh)


@router.get("/networks")
def networks(
    response: Response,
    refresh: bool = RefreshParam,
    _user: UserOut = Depends(require_builder),
) -> List[str]:
    return _list_live("networks", vsphere.list_networks, response, refresh=refresh)


@router.get("/datastore-clusters")
def datastore_clusters(
    response: Response,
    refresh: bool = RefreshParam,
    _user: UserOut = Depends(require_builder),
) -> List[str]:
    return _list_live(
        "datastore-clusters", vsphere.list_datastore_clusters, response, refresh=refresh
    )


@router.get("/datastores")
def datastores(
    response: Response,
    refresh: bool = RefreshParam,
    _user: UserOut = Depends(require_builder),
) -> List[str]:
    """Live datastore names. The build form does not expose these as a picker."""
    return _list_live("datastores", vsphere.list_datastores, response, refresh=refresh)
