"""VM lifecycle operations on top of ovbuilder.vsphere.

Used by the web self-service API so operators can power, snapshot, resize,
and reclaim VMs without opening the vSphere client.

Every helper refuses templates: Packer goldens (``ovbuilder-*``) are VMs in
vSphere and must never be powered, resized, or destroyed from here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pyVmomi import vim

from .vsphere import _wait_task

POWERED_ON = "poweredOn"
POWERED_OFF = "poweredOff"

MAX_CPUS = 128
MIN_MEMORY_MB = 512
MAX_MEMORY_MB = 4 * 1024 * 1024
MAX_DISK_GB = 62 * 1024


class VmOpsError(RuntimeError):
    """Base class for lifecycle failures the API maps to a 4xx."""


class VmNotFoundError(VmOpsError):
    pass


class VmStateError(VmOpsError):
    """The VM exists but is in a state that forbids the operation."""


def _power_state(vm) -> str:
    return str(getattr(vm.runtime, "powerState", ""))


def _is_template(vm) -> bool:
    config = getattr(vm, "config", None)
    return bool(getattr(config, "template", False))


def _search_root(si, datacenter_name: Optional[str]):
    content = si.RetrieveContent()
    if datacenter_name:
        for entity in content.rootFolder.childEntity:
            if isinstance(entity, vim.Datacenter) and entity.name == datacenter_name:
                return content, entity
    return content, content.rootFolder


def _find_vm(si, vm_name: str, datacenter_name: Optional[str] = None):
    content, root = _search_root(si, datacenter_name)
    view = content.viewManager.CreateContainerView(root, [vim.VirtualMachine], True)
    try:
        for vm in view.view:
            if vm.name == vm_name:
                return vm
    finally:
        view.Destroy()
    return None


def list_vms(si, datacenter_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return a compact inventory of non-template VMs visible to this session."""
    content, root = _search_root(si, datacenter_name)
    view = content.viewManager.CreateContainerView(root, [vim.VirtualMachine], True)
    try:
        rows = []
        for vm in view.view:
            try:
                if _is_template(vm):
                    continue
                runtime = vm.runtime
                config = vm.config
                summary = vm.summary
                guest = vm.guest
                hardware = getattr(config, "hardware", None) if config else None
                rows.append(
                    {
                        "name": vm.name,
                        "power_state": str(runtime.powerState),
                        "guest_os": getattr(config, "guestFullName", None) or getattr(config, "guestId", ""),
                        "ip": getattr(guest, "ipAddress", None),
                        "cpus": getattr(hardware, "numCPU", None) if hardware else None,
                        "memory_mb": getattr(hardware, "memoryMB", None) if hardware else None,
                        "disk_gb": _total_disk_gb(hardware) if hardware else None,
                        "uuid": getattr(config, "uuid", None),
                        "tools": str(getattr(guest, "toolsRunningStatus", "")),
                        "folder": getattr(getattr(vm, "parent", None), "name", None),
                        "overall_status": str(getattr(summary, "overallStatus", "")),
                    }
                )
            except Exception:
                continue
        return sorted(rows, key=lambda r: r["name"].lower())
    finally:
        view.Destroy()


def _require_vm(si, vm_name: str, datacenter_name: Optional[str] = None):
    vm = _find_vm(si, vm_name, datacenter_name)
    if vm is None:
        raise VmNotFoundError(f"VM not found in vCenter: {vm_name}")
    if _is_template(vm):
        raise VmStateError(f"{vm_name} is a template; lifecycle actions are not allowed")
    return vm


def vm_exists(si, vm_name: str, datacenter_name: Optional[str] = None) -> bool:
    vm = _find_vm(si, vm_name, datacenter_name)
    return vm is not None and not _is_template(vm)


def vm_uuid(si, vm_name: str, datacenter_name: Optional[str] = None) -> Optional[str]:
    vm = _find_vm(si, vm_name, datacenter_name)
    if vm is None or _is_template(vm):
        return None
    uuid = getattr(getattr(vm, "config", None), "uuid", None)
    return str(uuid).lower() if uuid else None


def power_state(si, vm_name: str, datacenter_name: Optional[str] = None) -> str:
    return _power_state(_require_vm(si, vm_name, datacenter_name))


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


def power_on(si, vm_name: str, datacenter_name: Optional[str] = None) -> Dict[str, Any]:
    vm = _require_vm(si, vm_name, datacenter_name)
    if _power_state(vm) == POWERED_ON:
        return {"name": vm_name, "changed": False, "power_state": POWERED_ON}
    _wait_task(vm.PowerOnVM_Task(), timeout_s=180)
    return {"name": vm_name, "changed": True, "power_state": POWERED_ON}


def power_off(si, vm_name: str, datacenter_name: Optional[str] = None) -> Dict[str, Any]:
    vm = _require_vm(si, vm_name, datacenter_name)
    if _power_state(vm) == POWERED_OFF:
        return {"name": vm_name, "changed": False, "power_state": POWERED_OFF}
    _wait_task(vm.PowerOffVM_Task(), timeout_s=180)
    return {"name": vm_name, "changed": True, "power_state": POWERED_OFF}


def reboot(si, vm_name: str, datacenter_name: Optional[str] = None) -> Dict[str, Any]:
    """Guest-OS restart via VMware Tools; falls back to a hard reset."""
    vm = _require_vm(si, vm_name, datacenter_name)
    if _power_state(vm) != POWERED_ON:
        raise VmStateError(f"{vm_name} is not powered on")
    try:
        vm.RebootGuest()
        return {"name": vm_name, "changed": True, "action": "rebootGuest"}
    except Exception:
        _wait_task(vm.ResetVM_Task(), timeout_s=180)
        return {"name": vm_name, "changed": True, "action": "reset"}


def reset(si, vm_name: str, datacenter_name: Optional[str] = None) -> Dict[str, Any]:
    """Hard reset (power-cycle) without asking the guest."""
    vm = _require_vm(si, vm_name, datacenter_name)
    if _power_state(vm) != POWERED_ON:
        raise VmStateError(f"{vm_name} is not powered on")
    _wait_task(vm.ResetVM_Task(), timeout_s=180)
    return {"name": vm_name, "changed": True, "action": "reset"}


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _snapshot_id(tree) -> str:
    return str(getattr(tree.snapshot, "_moId", None) or tree.id)


def _walk_snapshots(trees, parent_id: Optional[str] = None):
    for tree in trees or []:
        yield tree, parent_id
        yield from _walk_snapshots(getattr(tree, "childSnapshotList", None), _snapshot_id(tree))


def list_snapshots(si, vm_name: str, datacenter_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Flattened snapshot tree, oldest first. ``id`` is the snapshot moref."""
    vm = _require_vm(si, vm_name, datacenter_name)
    info = getattr(vm, "snapshot", None)
    if not info:
        return []
    current = getattr(getattr(info, "currentSnapshot", None), "_moId", None)
    rows = []
    for tree, parent_id in _walk_snapshots(info.rootSnapshotList):
        created = getattr(tree, "createTime", None)
        sid = _snapshot_id(tree)
        rows.append(
            {
                "id": sid,
                "name": tree.name,
                "description": getattr(tree, "description", "") or "",
                "created": created.isoformat() if hasattr(created, "isoformat") else None,
                "power_state": str(getattr(tree, "state", "")),
                "parent_id": parent_id,
                "current": current is not None and sid == str(current),
            }
        )
    return rows


def _require_snapshot(vm, vm_name: str, snapshot_id: str):
    info = getattr(vm, "snapshot", None)
    for tree, _parent in _walk_snapshots(info.rootSnapshotList if info else None):
        if _snapshot_id(tree) == snapshot_id:
            return tree
    raise VmNotFoundError(f"Snapshot {snapshot_id} not found on {vm_name}")


def create_snapshot(
    si,
    vm_name: str,
    snapshot_name: str,
    description: str = "",
    memory: bool = False,
    datacenter_name: Optional[str] = None,
) -> Dict[str, Any]:
    vm = _require_vm(si, vm_name, datacenter_name)
    include_memory = bool(memory) and _power_state(vm) == POWERED_ON
    _wait_task(
        vm.CreateSnapshot_Task(snapshot_name, description or "ovbuilder snapshot", include_memory, False),
        timeout_s=600,
    )
    return {"name": vm_name, "changed": True, "snapshot": snapshot_name, "memory": include_memory}


def revert_snapshot(
    si, vm_name: str, snapshot_id: str, datacenter_name: Optional[str] = None
) -> Dict[str, Any]:
    """Restore the VM to *snapshot_id*. Changes since that snapshot are lost."""
    vm = _require_vm(si, vm_name, datacenter_name)
    tree = _require_snapshot(vm, vm_name, snapshot_id)
    _wait_task(tree.snapshot.RevertToSnapshot_Task(), timeout_s=900)
    return {"name": vm_name, "changed": True, "action": "reverted", "snapshot": tree.name, "snapshot_id": snapshot_id}


def delete_snapshot(
    si,
    vm_name: str,
    snapshot_id: str,
    remove_children: bool = False,
    datacenter_name: Optional[str] = None,
) -> Dict[str, Any]:
    vm = _require_vm(si, vm_name, datacenter_name)
    tree = _require_snapshot(vm, vm_name, snapshot_id)
    _wait_task(tree.snapshot.RemoveSnapshot_Task(remove_children, True), timeout_s=1800)
    return {"name": vm_name, "changed": True, "action": "snapshot_deleted", "snapshot": tree.name, "snapshot_id": snapshot_id}


# ---------------------------------------------------------------------------
# Resize
# ---------------------------------------------------------------------------


def _disks(hardware) -> List[Any]:
    devices = getattr(hardware, "device", None) or []
    return [d for d in devices if isinstance(d, vim.vm.device.VirtualDisk)]


def _disk_gb(disk) -> int:
    return int((getattr(disk, "capacityInKB", 0) or 0) // (1024 * 1024))


def _total_disk_gb(hardware) -> Optional[int]:
    disks = _disks(hardware)
    if not disks:
        return None
    return sum(_disk_gb(d) for d in disks)


def plan_resize(
    vm,
    cpus: Optional[int] = None,
    memory_mb: Optional[int] = None,
    disk_gb: Optional[int] = None,
    disk_index: int = 0,
) -> "vim.vm.ConfigSpec":
    """Validate a resize against the VM's state and return the ConfigSpec.

    Raises ``ValueError`` for bad input and ``VmStateError`` when the change
    needs the VM powered off (no hot-add / hot-remove) or would shrink a disk.
    """
    if cpus is None and memory_mb is None and disk_gb is None:
        raise ValueError("Nothing to resize: set cpus, memory_mb, or disk_gb")

    name = vm.name
    config = vm.config
    hardware = config.hardware
    running = _power_state(vm) == POWERED_ON
    spec = vim.vm.ConfigSpec()
    changed = False

    if cpus is not None:
        if not 1 <= cpus <= MAX_CPUS:
            raise ValueError(f"cpus must be between 1 and {MAX_CPUS}")
        current = int(hardware.numCPU)
        if cpus != current:
            if running and cpus > current and not getattr(config, "cpuHotAddEnabled", False):
                raise VmStateError(f"{name}: CPU hot-add is disabled; power off to change vCPUs")
            if running and cpus < current and not getattr(config, "cpuHotRemoveEnabled", False):
                raise VmStateError(f"{name}: CPU hot-remove is disabled; power off to reduce vCPUs")
            spec.numCPUs = cpus
            cores = int(getattr(hardware, "numCoresPerSocket", 1) or 1)
            if cpus % cores:
                spec.numCoresPerSocket = 1
            changed = True

    if memory_mb is not None:
        if not MIN_MEMORY_MB <= memory_mb <= MAX_MEMORY_MB or memory_mb % 4:
            raise ValueError(
                f"memory_mb must be a multiple of 4 between {MIN_MEMORY_MB} and {MAX_MEMORY_MB}"
            )
        current = int(hardware.memoryMB)
        if memory_mb != current:
            if running and memory_mb < current:
                raise VmStateError(f"{name}: memory cannot be reduced while powered on")
            if running and not getattr(config, "memoryHotAddEnabled", False):
                raise VmStateError(f"{name}: memory hot-add is disabled; power off to change memory")
            spec.memoryMB = memory_mb
            changed = True

    if disk_gb is not None:
        disks = _disks(hardware)
        if not disks:
            raise VmStateError(f"{name} has no virtual disks")
        if not 0 <= disk_index < len(disks):
            raise ValueError(f"disk_index must be between 0 and {len(disks) - 1}")
        if not 1 <= disk_gb <= MAX_DISK_GB:
            raise ValueError(f"disk_gb must be between 1 and {MAX_DISK_GB}")
        disk = disks[disk_index]
        current = _disk_gb(disk)
        if disk_gb < current:
            raise VmStateError(f"{name}: disks can only grow ({current} GB -> {disk_gb} GB requested)")
        if disk_gb > current:
            if getattr(vm, "snapshot", None):
                raise VmStateError(f"{name}: delete snapshots before growing a disk")
            disk.capacityInKB = disk_gb * 1024 * 1024
            disk.capacityInBytes = disk_gb * 1024 * 1024 * 1024
            change = vim.vm.device.VirtualDeviceSpec()
            change.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
            change.device = disk
            spec.deviceChange = [change]
            changed = True

    if not changed:
        raise ValueError(f"{name} already has the requested size")
    return spec


def resize_vm(
    si,
    vm_name: str,
    cpus: Optional[int] = None,
    memory_mb: Optional[int] = None,
    disk_gb: Optional[int] = None,
    disk_index: int = 0,
    datacenter_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Change vCPU / memory and grow one virtual disk in a single reconfigure.

    Growing a disk does not extend the guest partition or filesystem.
    """
    vm = _require_vm(si, vm_name, datacenter_name)
    spec = plan_resize(vm, cpus=cpus, memory_mb=memory_mb, disk_gb=disk_gb, disk_index=disk_index)
    _wait_task(vm.ReconfigVM_Task(spec=spec), timeout_s=600)
    result: Dict[str, Any] = {"name": vm_name, "changed": True, "action": "resized"}
    if spec.numCPUs:
        result["cpus"] = spec.numCPUs
    if spec.memoryMB:
        result["memory_mb"] = spec.memoryMB
    if spec.deviceChange:
        result["disk_gb"] = disk_gb
        result["disk_index"] = disk_index
        result["note"] = "Guest partition/filesystem was not extended; grow it inside the OS."
    return result


# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------


def destroy_vm(si, vm_name: str, datacenter_name: Optional[str] = None) -> Dict[str, Any]:
    """Power off if needed, then destroy the VM and its disks. Irreversible."""
    vm = _require_vm(si, vm_name, datacenter_name)
    if _power_state(vm) != POWERED_OFF:
        _wait_task(vm.PowerOffVM_Task(), timeout_s=180)
    _wait_task(vm.Destroy_Task(), timeout_s=300)
    return {"name": vm_name, "changed": True, "action": "destroyed"}
