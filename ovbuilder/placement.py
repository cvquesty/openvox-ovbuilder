"""
Environment → datastore-cluster / compute-cluster mapping.

=============================================================================
WHY THIS MODULE EXISTS
=============================================================================
The web build form and the Celery worker must agree on what ``dev`` and
``prod`` mean. This module is the only place that resolves:

    environment key → Storage DRS cluster (and optional default compute cluster)

Sources (first hit wins per field, later sources overlay):
  1. ``environments`` in config.yaml (see ``OvbuilderConfig``)
  2. ``OVBUILDER_ENV_<KEY>_DATASTORE_CLUSTER`` / ``_CLUSTER`` / ``_LABEL``
  3. Built-in defaults (YAVIN-DEV / YAVIN-PROD) when nothing is configured

Operators never pick an individual LUN in the web UI; they pick an
environment and this mapping chooses the datastore cluster.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .config import (
    EnvironmentProfile,
    OvbuilderConfig,
    _default_environments,
    get_config_manager,
)
from .goldens import public_os_rows_from_config


def default_environments() -> Dict[str, EnvironmentProfile]:
    """Lab-oriented defaults shipped with ovbuilder (overridden on real sites)."""
    return _default_environments()


def _copy_envs(envs: Dict[str, EnvironmentProfile]) -> Dict[str, EnvironmentProfile]:
    return {key: profile.model_copy() for key, profile in envs.items()}


def _apply_env_overrides(
    envs: Dict[str, EnvironmentProfile],
) -> Dict[str, EnvironmentProfile]:
    """
    Overlay ``OVBUILDER_ENV_<KEY>_*`` variables onto the mapping.

    Longer suffixes are matched first so ``DEV_DATASTORE_CLUSTER`` does not
    look like a compute-cluster override for ``DEV_DATASTORE``.
    """
    out = _copy_envs(envs)
    prefix = "OVBUILDER_ENV_"
    suffixes = (
        ("_DATASTORE_CLUSTER", "datastore_cluster"),
        ("_CLUSTER", "cluster"),
        ("_LABEL", "label"),
    )
    for key, raw in os.environ.items():
        if not key.startswith(prefix) or raw is None:
            continue
        value = str(raw).strip()
        if not value:
            continue
        rest = key[len(prefix) :]
        for suffix, field in suffixes:
            if rest.endswith(suffix):
                env_key = rest[: -len(suffix)].lower()
                if not env_key:
                    break
                if env_key not in out:
                    out[env_key] = EnvironmentProfile(label=env_key)
                setattr(out[env_key], field, value)
                break
    return out


def resolved_environments(
    cfg: Optional[OvbuilderConfig] = None,
) -> Dict[str, EnvironmentProfile]:
    """Return the effective environment mapping (config + env overlays)."""
    if cfg is None:
        cfg = get_config_manager().load_config()
    configured = getattr(cfg, "environments", None) or {}
    if configured:
        base = {
            key: (
                value
                if isinstance(value, EnvironmentProfile)
                else EnvironmentProfile(**value)
                if isinstance(value, dict)
                else EnvironmentProfile(label=str(key), datastore_cluster=str(value))
            )
            for key, value in configured.items()
        }
    else:
        base = default_environments()
    return _apply_env_overrides(base)


def environments_as_dicts(cfg: Optional[OvbuilderConfig] = None) -> List[Dict[str, str]]:
    """JSON-ready rows for the web inventory API."""
    rows: List[Dict[str, str]] = []
    for key, profile in resolved_environments(cfg).items():
        rows.append(
            {
                "key": key,
                "label": profile.label or key,
                "datastore_cluster": profile.datastore_cluster or "",
                "cluster": profile.cluster or "",
            }
        )
    return rows


def datastore_cluster_for(
    environment: str,
    cfg: Optional[OvbuilderConfig] = None,
) -> str:
    """Resolve the Storage DRS cluster for an environment key."""
    envs = resolved_environments(cfg)
    key = (environment or "dev").strip().lower()
    profile = envs.get(key)
    if profile and profile.datastore_cluster:
        return profile.datastore_cluster
    # Unknown key: prefer the named default, then any configured cluster.
    fallback = envs.get("dev")
    if fallback and fallback.datastore_cluster:
        return fallback.datastore_cluster
    for item in envs.values():
        if item.datastore_cluster:
            return item.datastore_cluster
    return ""


def os_images_as_dicts(cfg: Optional[OvbuilderConfig] = None) -> List[Dict[str, str]]:
    """JSON-ready golden-image rows for the web OS picker (ovbuilder-* only)."""
    return public_os_rows_from_config(cfg)
