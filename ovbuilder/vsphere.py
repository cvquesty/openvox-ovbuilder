"""
vSphere discovery helpers for ovbuilder.

Connects to vCenter using the provided credentials and returns real
values for datacenters, clusters, datastores, networks, and ISOs
so that selection menus can be populated from actual inventory
instead of hardcoded defaults.
"""

import ssl
from typing import List, Optional

from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim


def connect(server: str, user: str, pwd: str, port: int = 443, ignore_ssl: bool = True):
    """Connect to vCenter. Returns ServiceInstance or raises on failure."""
    if ignore_ssl:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = None

    try:
        si = SmartConnect(
            host=server,
            user=user,
            pwd=pwd,
            port=port,
            sslContext=context,
        )
        return si
    except Exception as exc:
        raise RuntimeError(f"Failed to connect to vCenter {server}: {exc}") from exc


def _get_container_view(content, obj_type):
    return content.viewManager.CreateContainerView(
        content.rootFolder, [obj_type], True
    )


def list_datacenters(si) -> List[str]:
    content = si.RetrieveContent()
    names = []
    for dc in content.rootFolder.childEntity:
        if isinstance(dc, vim.Datacenter):
            names.append(dc.name)
    return sorted(names)


def list_clusters(si, datacenter_name: str) -> List[str]:
    content = si.RetrieveContent()
    dc = None
    for d in content.rootFolder.childEntity:
        if isinstance(d, vim.Datacenter) and d.name == datacenter_name:
            dc = d
            break
    if not dc:
        return []

    names = []
    for item in dc.hostFolder.childEntity:
        if isinstance(item, (vim.ClusterComputeResource, vim.ComputeResource)):
            names.append(item.name)
    return sorted(names)


def list_datastores(si, datacenter_name: str) -> List[str]:
    content = si.RetrieveContent()
    dc = None
    for d in content.rootFolder.childEntity:
        if isinstance(d, vim.Datacenter) and d.name == datacenter_name:
            dc = d
            break
    if not dc:
        return []

    names = [ds.name for ds in dc.datastore]
    return sorted(names)


def list_networks(si, datacenter_name: str) -> List[str]:
    content = si.RetrieveContent()
    dc = None
    for d in content.rootFolder.childEntity:
        if isinstance(d, vim.Datacenter) and d.name == datacenter_name:
            dc = d
            break
    if not dc:
        return []

    names = []
    for net in dc.network:
        names.append(net.name)
    return sorted(names)


def list_isos(si, datastore_name: str, datacenter_name: str) -> List[str]:
    """List .iso files on the given datastore (relative paths from datastore root)."""
    content = si.RetrieveContent()
    dc = None
    for d in content.rootFolder.childEntity:
        if isinstance(d, vim.Datacenter) and d.name == datacenter_name:
            dc = d
            break
    if not dc:
        return []

    ds = None
    for store in dc.datastore:
        if store.name == datastore_name:
            ds = store
            break
    if not ds or not ds.browser:
        return []

    search_spec = vim.host.DatastoreBrowser.SearchSpec()
    search_spec.query = [vim.host.DatastoreBrowser.IsoImageQuery()]
    search_spec.details = vim.host.DatastoreBrowser.FileInfo.Details(
        fileType=True, fileSize=True
    )
    search_spec.sortFoldersFirst = True

    datastore_path = "[%s]" % datastore_name

    try:
        task = ds.browser.SearchDatastoreSubFolders_Task(
            datastore_path, search_spec
        )
        # Wait for task completion
        while task.info.state == vim.TaskInfo.State.running:
            pass

        if task.info.state != vim.TaskInfo.State.success:
            return []

        results = []
        for res in task.info.result or []:
            for f in res.file or []:
                if f.path and f.path.lower().endswith(".iso"):
                    fp = res.folderPath or ""
                    # Strip leading [datastore_name] prefix (and any whitespace/slashes after it)
                    # e.g. "[isos] " or "[isos]/subdir/" -> clean relative path
                    if fp.startswith("["):
                        close = fp.find("]")
                        if close != -1:
                            fp = fp[close + 1 :]
                    path = (fp + (f.path or "")).strip().lstrip("/")
                    if path:
                        results.append(path)
        return sorted(set(results))
    except Exception:
        # Fallback to manual entry
        return []


def disconnect(si):
    try:
        Disconnect(si)
    except Exception:
        pass


def _wait_task(task, timeout_s: int = 120):
    """Block until a vSphere task completes (or raise)."""
    import time

    deadline = time.time() + timeout_s
    while task.info.state in (
        vim.TaskInfo.State.running,
        vim.TaskInfo.State.queued,
    ):
        if time.time() > deadline:
            raise TimeoutError(f"vSphere task timed out after {timeout_s}s: {task.info}")
        time.sleep(0.5)
    if task.info.state != vim.TaskInfo.State.success:
        msg = getattr(task.info.error, "msg", None) or str(task.info.error or task.info.state)
        raise RuntimeError(f"vSphere task failed: {msg}")
    return task.info.result


def find_vm_by_name(si, vm_name: str):
    """Return VirtualMachine managed object or None."""
    content = si.RetrieveContent()
    view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True
    )
    try:
        for vm in view.view:
            if vm.name == vm_name:
                return vm
    finally:
        view.Destroy()
    return None


def disconnect_install_media(si, vm_name: str) -> dict:
    """
    Detach datastore ISO(s) from all CD/DVD devices on a VM and leave them
    as disconnected client devices.

    Why: Terraform ISO mode leaves the install media mounted. That:
      - locks the ISO file on the datastore (other builds fail with "locked")
      - can re-boot the installer on the next reboot

    Call this after the OS is installed and *before* the guest reboots into
    the installed system (or immediately after first reboot if needed).

    Returns a summary dict: {changed: bool, devices: int, detail: str}
    """
    vm = find_vm_by_name(si, vm_name)
    if vm is None:
        raise RuntimeError(f"VM not found in vCenter: {vm_name}")

    device_changes = []
    for device in vm.config.hardware.device:
        if not isinstance(device, vim.vm.device.VirtualCdrom):
            continue

        # Re-point backing to an empty client device (no datastore ISO).
        backing = vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo()
        backing.deviceName = ""
        backing.exclusive = False
        device.backing = backing

        connectable = vim.vm.device.VirtualDevice.ConnectInfo()
        connectable.connected = False
        connectable.startConnected = False
        connectable.allowGuestControl = True
        device.connectable = connectable

        spec = vim.vm.device.VirtualDeviceSpec()
        spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
        spec.device = device
        device_changes.append(spec)

    if not device_changes:
        return {
            "changed": False,
            "devices": 0,
            "detail": "No CD/DVD devices found on VM",
        }

    cfg = vim.vm.ConfigSpec()
    cfg.deviceChange = device_changes

    # Prefer hard disk for next boot so firmware doesn't return to the empty CD.
    try:
        boot = vim.vm.BootOptions()
        boot.bootOrder = [
            vim.vm.BootOptions.BootableDiskDevice(deviceKey=d.key)
            for d in vm.config.hardware.device
            if isinstance(d, vim.vm.device.VirtualDisk)
        ][:1]
        if boot.bootOrder:
            cfg.bootOptions = boot
    except Exception:
        pass

    task = vm.ReconfigVM_Task(spec=cfg)
    _wait_task(task)

    return {
        "changed": True,
        "devices": len(device_changes),
        "detail": f"Disconnected install media on {len(device_changes)} CD/DVD device(s)",
    }
