"""Per-VM Terraform state for lifecycle actions.

Builds keep each VM's Terraform state, var file, and module copy under
``tfstate/<hostname>`` and hold ``hostname_lock`` on that directory for the
whole init + apply (see ``terraform.py``). Lifecycle actions take the same
lock, so nobody powers off, resizes, or destroys a VM while a build of that
hostname is still applying.

After a destroy, the per-VM directory is retired: state files move to
``tfstate/.retired/<hostname>-<UTC timestamp>/`` for audit, and the var file
(which holds the vSphere password), module copy, and provider cache are
deleted. The next build of that hostname starts from empty state.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set

from .config import get_config_manager
from .terraform import (  # noqa: F401 — StateLockedError re-exported for callers
    StateLockedError,
    hostname_lock,
    sanitize_state_key,
    state_dir_for_vm,
)

RETIRED_DIR = ".retired"
LOCK_FILE = ".ovbuilder.lock"
_STATE_FILES = ("terraform.tfstate", "terraform.tfstate.backup", "terraform.tfstate.migrated")


def _data_dir(data_dir: Optional[Path]) -> Path:
    return Path(data_dir) if data_dir is not None else Path(get_config_manager().data_dir)


def existing_state_dir(vm_name: str, data_dir: Optional[Path] = None) -> Optional[Path]:
    """The per-VM directory if a build ever ran for *vm_name* (never creates it)."""
    path = _data_dir(data_dir) / "tfstate" / sanitize_state_key(vm_name)
    return path if path.is_dir() else None


@contextlib.contextmanager
def vm_lock(
    vm_name: str, data_dir: Optional[Path] = None, *, create: bool = True
) -> Iterator[Optional[Path]]:
    """Hold the build's per-hostname lock; raises ``StateLockedError`` if busy.

    With ``create=False`` and no state directory, yields ``None`` without
    locking (the VM was never built by ovbuilder on this host).
    """
    sdir = state_dir_for_vm(vm_name, data_dir=data_dir) if create else existing_state_dir(vm_name, data_dir)
    if sdir is None:
        yield None
        return
    with hostname_lock(sdir):
        yield sdir


def state_vm_uuids(vm_name: str, data_dir: Optional[Path] = None) -> Set[str]:
    """Instance UUIDs of ``vsphere_virtual_machine`` resources in local state.

    Proves a vCenter VM was created by this host's Terraform (e.g. a VM left
    behind by a cancelled or failed build). Empty for remote backends.
    """
    sdir = existing_state_dir(vm_name, data_dir)
    if sdir is None:
        return set()
    uuids: Set[str] = set()
    for fname in _STATE_FILES:
        path = sdir / fname
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for resource in data.get("resources") or []:
            if resource.get("type") != "vsphere_virtual_machine":
                continue
            for inst in resource.get("instances") or []:
                attrs = inst.get("attributes") or {}
                for key in ("uuid", "id"):
                    if attrs.get(key):
                        uuids.add(str(attrs[key]).lower())
    return uuids


def retire_state(sdir: Path, data_dir: Optional[Path] = None) -> Optional[Path]:
    """Move state files aside and delete the rest; caller holds the lock.

    The lock file stays so the lock held by the caller remains valid.
    Returns the retired directory, or ``None`` if there was no state.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    retired: Optional[Path] = None
    for fname in _STATE_FILES:
        src = sdir / fname
        if src.is_file():
            if retired is None:
                retired = _data_dir(data_dir) / "tfstate" / RETIRED_DIR / f"{sdir.name}-{stamp}"
                retired.mkdir(parents=True, exist_ok=True)
                try:
                    retired.chmod(0o700)
                except OSError:
                    pass
            src.rename(retired / fname)
    for entry in sdir.iterdir():
        if entry.name == LOCK_FILE:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
    return retired


def destroy_with_state(
    si,
    vm_name: str,
    datacenter_name: Optional[str] = None,
    *,
    keep_state: bool = False,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Destroy *vm_name* under the hostname lock, then retire its state.

    A VM that is already gone (e.g. a cancelled build that never got far)
    still has its leftover state retired.
    """
    from . import vmops

    with vm_lock(vm_name, data_dir, create=False) as sdir:
        if vmops.vm_exists(si, vm_name, datacenter_name):
            result = vmops.destroy_vm(si, vm_name, datacenter_name)
        elif sdir is not None:
            result = {"name": vm_name, "changed": False, "action": "already_absent"}
        else:
            raise vmops.VmNotFoundError(f"VM not found in vCenter: {vm_name}")
        if sdir is not None and not keep_state:
            retired = retire_state(sdir, data_dir)
            result["state_retired_to"] = str(retired) if retired else None
    return result
