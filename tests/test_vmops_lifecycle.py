"""VM lifecycle helpers (issue #5): power, snapshots, resize — no live vCenter."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pyVmomi import vim

from ovbuilder import vmops

GB_KB = 1024 * 1024


def _disk(gb: int):
    disk = vim.vm.device.VirtualDisk()
    disk.key = 2000
    disk.capacityInKB = gb * GB_KB
    return disk


def _vm(
    name="web01",
    power="poweredOff",
    cpus=2,
    memory_mb=4096,
    disks=(80,),
    template=False,
    cpu_hot_add=False,
    cpu_hot_remove=False,
    mem_hot_add=False,
    snapshot=None,
):
    hardware = SimpleNamespace(
        numCPU=cpus,
        numCoresPerSocket=1,
        memoryMB=memory_mb,
        device=[_disk(gb) for gb in disks],
    )
    config = SimpleNamespace(
        template=template,
        hardware=hardware,
        cpuHotAddEnabled=cpu_hot_add,
        cpuHotRemoveEnabled=cpu_hot_remove,
        memoryHotAddEnabled=mem_hot_add,
        uuid="4210ABCD-0000",
    )
    vm = MagicMock()
    vm.name = name
    vm.runtime.powerState = power
    vm.config = config
    vm.snapshot = snapshot
    return vm


def _snap(moid, name, children=(), created=None):
    return SimpleNamespace(
        snapshot=SimpleNamespace(_moId=moid, RevertToSnapshot_Task=MagicMock(), RemoveSnapshot_Task=MagicMock()),
        id=int(moid.split("-")[-1]),
        name=name,
        description="",
        createTime=created or datetime(2026, 11, 1, tzinfo=timezone.utc),
        state="poweredOff",
        childSnapshotList=list(children),
    )


@pytest.fixture
def no_wait():
    with patch.object(vmops, "_wait_task", return_value=None) as wait:
        yield wait


def _found(vm):
    return patch.object(vmops, "_find_vm", return_value=vm)


# --- templates / lookup ------------------------------------------------------


def test_templates_are_refused(no_wait):
    vm = _vm(template=True)
    with _found(vm), pytest.raises(vmops.VmStateError, match="template"):
        vmops.destroy_vm(object(), "ovbuilder-ubuntu-24.04")
    vm.Destroy_Task.assert_not_called()


def test_missing_vm_is_not_found():
    with _found(None), pytest.raises(vmops.VmNotFoundError):
        vmops.power_on(object(), "ghost")


def test_vm_uuid_lowercased_and_none_for_templates():
    with _found(_vm()):
        assert vmops.vm_uuid(object(), "web01") == "4210abcd-0000"
    with _found(_vm(template=True)):
        assert vmops.vm_uuid(object(), "web01") is None


# --- power ---------------------------------------------------------------------


def test_power_on_is_idempotent(no_wait):
    vm = _vm(power="poweredOn")
    with _found(vm):
        assert vmops.power_on(object(), "web01")["changed"] is False
    vm.PowerOnVM_Task.assert_not_called()


def test_reset_requires_powered_on(no_wait):
    with _found(_vm(power="poweredOff")), pytest.raises(vmops.VmStateError):
        vmops.reset(object(), "web01")
    vm = _vm(power="poweredOn")
    with _found(vm):
        assert vmops.reset(object(), "web01")["action"] == "reset"
    vm.ResetVM_Task.assert_called_once()


def test_destroy_powers_off_first(no_wait):
    vm = _vm(power="poweredOn")
    with _found(vm):
        vmops.destroy_vm(object(), "web01")
    vm.PowerOffVM_Task.assert_called_once()
    vm.Destroy_Task.assert_called_once()


# --- snapshots -------------------------------------------------------------------


def _snapshot_tree():
    child = _snap("snapshot-2", "after-patch")
    root = _snap("snapshot-1", "baseline", children=[child])
    info = SimpleNamespace(rootSnapshotList=[root], currentSnapshot=SimpleNamespace(_moId="snapshot-2"))
    return info, root, child


def test_list_snapshots_flattens_tree():
    info, _root, _child = _snapshot_tree()
    with _found(_vm(snapshot=info)):
        rows = vmops.list_snapshots(object(), "web01")
    assert [r["name"] for r in rows] == ["baseline", "after-patch"]
    assert rows[0]["parent_id"] is None and rows[1]["parent_id"] == "snapshot-1"
    assert [r["current"] for r in rows] == [False, True]
    assert rows[0]["created"].startswith("2026-11-01")


def test_list_snapshots_empty():
    with _found(_vm(snapshot=None)):
        assert vmops.list_snapshots(object(), "web01") == []


def test_revert_and_delete_target_the_right_snapshot(no_wait):
    info, root, child = _snapshot_tree()
    with _found(_vm(snapshot=info)):
        assert vmops.revert_snapshot(object(), "web01", "snapshot-1")["snapshot"] == "baseline"
        vmops.delete_snapshot(object(), "web01", "snapshot-2", remove_children=False)
    root.snapshot.RevertToSnapshot_Task.assert_called_once()
    child.snapshot.RemoveSnapshot_Task.assert_called_once_with(False, True)
    root.snapshot.RemoveSnapshot_Task.assert_not_called()


def test_unknown_snapshot_is_not_found(no_wait):
    info, _root, _child = _snapshot_tree()
    with _found(_vm(snapshot=info)), pytest.raises(vmops.VmNotFoundError):
        vmops.revert_snapshot(object(), "web01", "snapshot-99")


def test_memory_snapshot_only_when_running(no_wait):
    vm = _vm(power="poweredOff")
    with _found(vm):
        result = vmops.create_snapshot(object(), "web01", "pre", memory=True)
    assert result["memory"] is False
    assert vm.CreateSnapshot_Task.call_args.args[2] is False


# --- resize ----------------------------------------------------------------------


def test_resize_cold_vm_cpu_memory_disk():
    spec = vmops.plan_resize(_vm(), cpus=4, memory_mb=8192, disk_gb=120)
    assert spec.numCPUs == 4
    assert spec.memoryMB == 8192
    (change,) = spec.deviceChange
    assert change.operation == vim.vm.device.VirtualDeviceSpec.Operation.edit
    assert change.device.capacityInKB == 120 * GB_KB


def test_resize_running_without_hot_add_is_refused():
    with pytest.raises(vmops.VmStateError, match="hot-add"):
        vmops.plan_resize(_vm(power="poweredOn"), cpus=4)
    with pytest.raises(vmops.VmStateError, match="hot-add"):
        vmops.plan_resize(_vm(power="poweredOn"), memory_mb=8192)


def test_resize_running_with_hot_add_allowed_but_never_shrinks_memory():
    vm = _vm(power="poweredOn", cpu_hot_add=True, mem_hot_add=True)
    spec = vmops.plan_resize(vm, cpus=4, memory_mb=8192)
    assert spec.numCPUs == 4 and spec.memoryMB == 8192
    with pytest.raises(vmops.VmStateError, match="reduced"):
        vmops.plan_resize(vm, memory_mb=2048)
    with pytest.raises(vmops.VmStateError, match="hot-remove"):
        vmops.plan_resize(vm, cpus=1)


def test_disks_only_grow_and_not_with_snapshots():
    with pytest.raises(vmops.VmStateError, match="only grow"):
        vmops.plan_resize(_vm(disks=(80,)), disk_gb=40)
    info, _root, _child = _snapshot_tree()
    with pytest.raises(vmops.VmStateError, match="snapshots"):
        vmops.plan_resize(_vm(snapshot=info), disk_gb=100)


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"cpus": 0}, {"cpus": 500}, {"memory_mb": 100}, {"memory_mb": 4097}, {"disk_gb": 100, "disk_index": 3}],
)
def test_resize_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        vmops.plan_resize(_vm(), **kwargs)


def test_resize_noop_is_rejected():
    with pytest.raises(ValueError, match="already"):
        vmops.plan_resize(_vm(cpus=2), cpus=2)


def test_resize_vm_reports_guest_note(no_wait):
    vm = _vm()
    with _found(vm):
        result = vmops.resize_vm(object(), "web01", disk_gb=100)
    vm.ReconfigVM_Task.assert_called_once()
    assert result["disk_gb"] == 100
    assert "filesystem" in result["note"]
