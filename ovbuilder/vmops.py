"""VM lifecycle operations on top of ovbuilder.vsphere.

Used by the web self-service API so operators can power, snapshot, and
reclaim VMs without opening the vSphere client.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pyVmomi import vim

from .vsphere import _wait_task, find_vm_by_name


def list_vms(si, datacenter_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return a compact inventory of VMs visible to this session."""
    content = si.RetrieveContent()
    root = content.rootFolder
    if datacenter_name:
        for entity in content.rootFolder.childEntity:
            if isinstance(entity, vim.Datacenter) and entity.name == datacenter_name:
                root = entity
                break
    view = content.viewManager.CreateContainerView(root, [vim.VirtualMachine], True)
    try:
        rows = []
        for vm in view.view:
            try:
                runtime = vm.runtime
                config = vm.config
                summary = vm.summary
                guest = vm.guest
                rows.append(
                    {
                        "name": vm.name,
                        "power_state": str(runtime.powerState),
                        "guest_os": getattr(config, "guestFullName", None) or getattr(config, "guestId", ""),
                        "ip": getattr(guest, "ipAddress", None),
                        "cpus": getattr(config.hardware, "numCPU", None) if config and config.hardware else None,
                        "memory_mb": getattr(config.hardware, "memoryMB", None) if config and config.hardware else None,
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


def _require_vm(si, vm_name: str):
    vm = find_vm_by_name(si, vm_name)
    if vm is None:
        raise RuntimeError(f"VM not found in vCenter: {vm_name}")
    return vm


def power_on(si, vm_name: str) -> Dict[str, Any]:
    vm = _require_vm(si, vm_name)
    if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOn:
        return {"name": vm_name, "changed": False, "power_state": "poweredOn"}
    _wait_task(vm.PowerOnVM_Task(), timeout_s=180)
    return {"name": vm_name, "changed": True, "power_state": "poweredOn"}


def power_off(si, vm_name: str) -> Dict[str, Any]:
    vm = _require_vm(si, vm_name)
    if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOff:
        return {"name": vm_name, "changed": False, "power_state": "poweredOff"}
    _wait_task(vm.PowerOffVM_Task(), timeout_s=180)
    return {"name": vm_name, "changed": True, "power_state": "poweredOff"}


def reboot(si, vm_name: str) -> Dict[str, Any]:
    vm = _require_vm(si, vm_name)
    try:
        vm.RebootGuest()
        return {"name": vm_name, "changed": True, "action": "rebootGuest"}
    except Exception:
        _wait_task(vm.ResetVM_Task(), timeout_s=180)
        return {"name": vm_name, "changed": True, "action": "reset"}


def create_snapshot(si, vm_name: str, snapshot_name: str, description: str = "") -> Dict[str, Any]:
    vm = _require_vm(si, vm_name)
    _wait_task(
        vm.CreateSnapshot_Task(snapshot_name, description or "ovbuilder snapshot", False, False),
        timeout_s=600,
    )
    return {"name": vm_name, "changed": True, "snapshot": snapshot_name}


def destroy_vm(si, vm_name: str) -> Dict[str, Any]:
    """Power off if needed, then destroy the VM. Irreversible."""
    vm = _require_vm(si, vm_name)
    if vm.runtime.powerState != vim.VirtualMachinePowerState.poweredOff:
        _wait_task(vm.PowerOffVM_Task(), timeout_s=180)
    _wait_task(vm.Destroy_Task(), timeout_s=300)
    return {"name": vm_name, "changed": True, "action": "destroyed"}
