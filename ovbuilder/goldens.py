"""
ovbuilder-* golden template discovery helpers (no user-facing DC/datastore).

The CLI and web OS pickers show only a template name plus a short label.
Source datacenter, datastore, and UUID stay internal so Terraform can clone
across datacenters without extra interview questions.

Selectable templates are restricted to inventory names matching ``ovbuilder-*``.
When the same name exists in more than one datacenter, one row is kept:

  1. configured home datacenter (``golden_home_datacenter`` / env), if present
  2. newest (createDate / changeVersion)
  3. first by datacenter name (stable)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import vsphere
from .config import GoldenImage, OvbuilderConfig, get_config_manager

GOLDEN_NAME_PREFIX = "ovbuilder-"
COMPUTE_TYPE_CLUSTER = "cluster"
COMPUTE_TYPE_HOST = "host"

_HOME_DC_ENV = "OVBUILDER_GOLDEN_HOME_DATACENTER"


def _meta_field(meta: Any, field: str, default: str = "") -> str:
    """Read a GoldenImage attribute or dict key without mixing the two."""
    if meta is None:
        return default
    if isinstance(meta, dict):
        val = meta.get(field)
    else:
        val = getattr(meta, field, None)
    if val is None or val == "":
        return default
    return str(val)


def is_ovbuilder_template_name(name: Optional[str]) -> bool:
    """True when *name* is a selectable golden (``ovbuilder-*``)."""
    return bool(name) and str(name).startswith(GOLDEN_NAME_PREFIX)


def home_datacenter(cfg: Optional[OvbuilderConfig] = None) -> str:
    """Configured home DC used only for silent duplicate resolution."""
    env_home = (os.environ.get(_HOME_DC_ENV) or "").strip()
    if env_home:
        return env_home
    if cfg is None:
        try:
            cfg = get_config_manager().load_config()
        except Exception:
            return ""
    explicit = (getattr(cfg, "golden_home_datacenter", None) or "").strip()
    if explicit:
        return explicit
    return ""


@dataclass
class GoldenTemplate:
    """One discovered (or config-fallback) Packer golden."""

    name: str
    datacenter: str = ""
    datastore: str = ""
    guest_id: str = ""
    default_user: str = ""
    uuid: str = ""
    label: str = ""
    key: str = ""
    created: Optional[datetime] = None
    change_version: str = ""

    def public_row(self) -> Dict[str, str]:
        """Fields the CLI table / web OS picker may show."""
        return {
            "key": self.key or self.name,
            "label": self.label or self.name,
            "default_user": self.default_user or "",
            "name": self.name,
        }


def golden_key(name: str, images: Optional[Dict[str, GoldenImage]] = None) -> str:
    """Stable picker / ``--os`` key for a template inventory name."""
    images = images or {}
    for key, meta in images.items():
        if _meta_field(meta, "template") == name:
            return str(key)
    if name.startswith(GOLDEN_NAME_PREFIX):
        return name[len(GOLDEN_NAME_PREFIX) :]
    return name


def human_label(name: str, images: Optional[Dict[str, GoldenImage]] = None) -> str:
    """Short operator-facing OS label (never includes a datacenter)."""
    images = images or {}
    for meta in images.values():
        description = _meta_field(meta, "description")
        if _meta_field(meta, "template") == name and description:
            return description
    slug = (
        name[len(GOLDEN_NAME_PREFIX) :]
        if name.startswith(GOLDEN_NAME_PREFIX)
        else name
    )
    pretty: List[str] = []
    for part in slug.replace("_", "-").split("-"):
        if not part:
            continue
        lower = part.lower()
        if part.replace(".", "").isdigit():
            pretty.append(part)
        elif lower == "almalinux":
            pretty.append("AlmaLinux")
        elif lower == "ubuntu":
            pretty.append("Ubuntu")
        elif lower == "lts":
            pretty.append("LTS")
        else:
            pretty.append(part.capitalize())
    return " ".join(pretty) if pretty else name


def infer_default_user(
    name: str,
    guest_id: str = "",
    images: Optional[Dict[str, GoldenImage]] = None,
) -> str:
    """SSH user baked into the golden, inferred from name / guest_id / config."""
    images = images or {}
    for meta in images.values():
        user = _meta_field(meta, "default_user")
        if _meta_field(meta, "template") == name and user:
            return user
    blob = f"{name} {guest_id}".lower()
    if "ubuntu" in blob:
        return "ubuntu"
    if "alma" in blob:
        return "almalinux"
    if "rocky" in blob:
        return "rocky"
    if "centos" in blob:
        return "centos"
    if "rhel" in blob or "redhat" in blob:
        return "cloud-user"
    if "debian" in blob:
        return "debian"
    return "root"


def infer_guest_id(
    name: str,
    guest_id: str = "",
    images: Optional[Dict[str, GoldenImage]] = None,
) -> str:
    """Prefer the live guest_id; fall back to a matching config entry."""
    if guest_id:
        return guest_id
    images = images or {}
    for meta in images.values():
        configured = _meta_field(meta, "guest_id")
        if _meta_field(meta, "template") == name and configured:
            return configured
    return ""


def enrich(
    golden: GoldenTemplate,
    images: Optional[Dict[str, GoldenImage]] = None,
) -> GoldenTemplate:
    """Fill key / label / login user / guest_id without exposing placement."""
    images = images or {}
    golden.key = golden.key or golden_key(golden.name, images)
    golden.label = golden.label or human_label(golden.name, images)
    golden.default_user = golden.default_user or infer_default_user(
        golden.name, golden.guest_id, images
    )
    golden.guest_id = infer_guest_id(golden.name, golden.guest_id, images)
    return golden


def _as_naive(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.min
    if getattr(dt, "tzinfo", None) is not None:
        try:
            return dt.replace(tzinfo=None)
        except Exception:
            return datetime.min
    return dt


def _recency(golden: GoldenTemplate) -> tuple:
    return (_as_naive(golden.created), golden.change_version or "")


def dedupe_goldens(
    goldens: Sequence[GoldenTemplate],
    home_datacenter_name: str = "",
) -> List[GoldenTemplate]:
    """
    Collapse duplicate template names to a single row.

    Never asks which datacenter. Home DC wins when configured and present;
    otherwise newest; otherwise first by datacenter name.
    """
    by_name: Dict[str, List[GoldenTemplate]] = {}
    for item in goldens:
        if not item.name:
            continue
        by_name.setdefault(item.name, []).append(item)

    chosen: List[GoldenTemplate] = []
    home = (home_datacenter_name or "").strip()
    for _name, group in by_name.items():
        if len(group) == 1:
            chosen.append(group[0])
            continue
        pool = list(group)
        if home:
            home_hits = [g for g in pool if g.datacenter == home]
            if home_hits:
                pool = home_hits
                if len(pool) == 1:
                    chosen.append(pool[0])
                    continue
        dated = [g for g in pool if g.created is not None or g.change_version]
        if dated:
            chosen.append(max(dated, key=_recency))
            continue
        pool.sort(key=lambda g: (g.datacenter or "", g.uuid or "", g.datastore or ""))
        chosen.append(pool[0])
    chosen.sort(key=lambda g: ((g.label or g.name).lower(), g.name.lower()))
    return chosen


def goldens_from_config(cfg: Optional[OvbuilderConfig] = None) -> List[GoldenTemplate]:
    """Config-backed picker rows, still restricted to ``ovbuilder-*`` names."""
    if cfg is None:
        cfg = get_config_manager().load_config()
    images = getattr(cfg, "golden_images", None) or {}
    rows: List[GoldenTemplate] = []
    for key, meta in images.items():
        template = _meta_field(meta, "template")
        if not is_ovbuilder_template_name(template):
            continue
        item = GoldenTemplate(
            name=template,
            guest_id=_meta_field(meta, "guest_id"),
            default_user=_meta_field(meta, "default_user"),
            label=_meta_field(meta, "description", default=str(key)),
            key=str(key),
        )
        rows.append(enrich(item, images))
    return rows


def templates_from_raw(
    raw: Iterable[Dict[str, Any]],
    cfg: Optional[OvbuilderConfig] = None,
    home_datacenter_name: str = "",
) -> List[GoldenTemplate]:
    """Turn pyVmomi scan dicts into deduped, enriched picker rows."""
    if cfg is None:
        try:
            cfg = get_config_manager().load_config()
        except Exception:
            cfg = None
    images = getattr(cfg, "golden_images", None) or {} if cfg is not None else {}
    goldens: List[GoldenTemplate] = []
    for row in raw or []:
        name = str((row or {}).get("name") or "")
        if not is_ovbuilder_template_name(name):
            continue
        created = (row or {}).get("created")
        if created is not None and not isinstance(created, datetime):
            created = None
        item = GoldenTemplate(
            name=name,
            datacenter=str((row or {}).get("datacenter") or ""),
            datastore=str((row or {}).get("datastore") or ""),
            guest_id=str((row or {}).get("guest_id") or ""),
            uuid=str((row or {}).get("uuid") or ""),
            created=created,
            change_version=str((row or {}).get("change_version") or ""),
        )
        goldens.append(enrich(item, images))
    return dedupe_goldens(
        goldens,
        home_datacenter_name=home_datacenter_name or home_datacenter(cfg),
    )


def discover_goldens(
    si,
    cfg: Optional[OvbuilderConfig] = None,
    home_datacenter_name: str = "",
) -> List[GoldenTemplate]:
    """Live vCenter scan for ``ovbuilder-*`` templates in every datacenter."""
    raw = vsphere.list_golden_templates(si)
    return templates_from_raw(
        raw, cfg=cfg, home_datacenter_name=home_datacenter_name
    )


def match_golden(
    os_image: str,
    goldens: Sequence[GoldenTemplate],
) -> Optional[GoldenTemplate]:
    """
    Resolve ``--os`` / web ``os_image`` against a picker list.

    Accepts config keys (``ubuntu-24.04``), full inventory names
    (``ovbuilder-ubuntu-24.04``), and the ``ovbuilder-`` prefix form.
    """
    needle = (os_image or "").strip()
    if not needle:
        return None
    for item in goldens:
        if needle in (item.key, item.name):
            return item
    prefixed = (
        needle
        if needle.startswith(GOLDEN_NAME_PREFIX)
        else GOLDEN_NAME_PREFIX + needle
    )
    for item in goldens:
        if item.name == prefixed or item.key == prefixed:
            return item
    return None


def resolve_os_image(
    os_image: str,
    live: Sequence[GoldenTemplate],
    cfg: Optional[OvbuilderConfig] = None,
) -> GoldenTemplate:
    """
    Pick one golden for a build.

    Live inventory wins. Config is only used when live did not match
    (offline fallback). Raises ``KeyError`` when nothing matches.
    """
    matched = match_golden(os_image, live)
    if matched:
        return matched
    fallback = goldens_from_config(cfg)
    matched = match_golden(os_image, fallback)
    if matched:
        return matched
    available = sorted(
        {
            *(g.key for g in live if g.key),
            *(g.name for g in live if g.name),
            *(g.key for g in fallback if g.key),
            *(g.name for g in fallback if g.name),
        }
    )
    hint = ", ".join(available) if available else "(none discovered)"
    raise KeyError(f"Unknown OS {os_image!r}. Available: {hint}")


def public_os_rows(goldens: Sequence[GoldenTemplate]) -> List[Dict[str, str]]:
    """JSON-ready OS picker rows (no datacenter / datastore fields)."""
    return [g.public_row() for g in goldens]


def public_os_rows_from_config(
    cfg: Optional[OvbuilderConfig] = None,
) -> List[Dict[str, str]]:
    """Config-only OS rows for tests and vCenter-down fallback."""
    return public_os_rows(goldens_from_config(cfg))
