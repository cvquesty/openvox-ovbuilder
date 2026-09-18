"""
vSphere / vCenter inventory and VM operations for ovbuilder.

=============================================================================
WHY THIS MODULE EXISTS
=============================================================================
ovbuilder must not hardcode datacenter/cluster/datastore/network names.
Operators select real inventory from live vCenter menus. This module is the
thin pyVmomi boundary:

  connect → list_* (inventory) → disconnect_install_media (post-ISO hygiene)

All functions take an open ServiceInstance (si) from connect(), except
connect() / disconnect() themselves.

=============================================================================
SECURITY NOTES
=============================================================================
* Credentials are never logged or written by this module.
* ignore_ssl=True is the lab default (self-signed vCenter certs are common).
  Production can pass ignore_ssl=False when a proper CA chain is installed.
* disconnect_install_media only reconfigures CD/DVD devices; it does not
  delete VMs or datastores.
"""

from __future__ import annotations

import ssl
import time
from typing import Any, List, Optional

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------

def connect(
    server: str,
    user: str,
    pwd: str,
    port: int = 443,
    ignore_ssl: bool = True,
):
    """
    Open a pyVmomi session to vCenter / ESXi.

    Parameters
    ----------
    server : str
        FQDN or IP of vCenter (e.g. vc01.example.com).
    user, pwd : str
        SSO or local credentials (not stored here).
    port : int
        HTTPS port (default 443).
    ignore_ssl : bool
        If True, skip certificate hostname/CA checks (lab convenience).

    Returns
    -------
    ServiceInstance
        Use with list_* helpers; always pair with disconnect(si).

    Raises
    ------
    RuntimeError
        On authentication or network failure (wraps underlying exception).
    """
    # Build an SSL context that either verifies or deliberately does not.
    if ignore_ssl:
        # Lab path: many vCenters use self-signed or internal CAs.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        # Production path: system trust store applies.
        context = None

    try:
        # SmartConnect negotiates SOAP + session cookie for subsequent API calls.
        si = SmartConnect(
            host=server,
            user=user,
            pwd=pwd,
            port=port,
            sslContext=context,
        )
        return si
    except Exception as exc:
        # Never include pwd in the message.
        raise RuntimeError(f"Failed to connect to vCenter {server}: {exc}") from exc


def disconnect(si) -> None:
    """
    Close a ServiceInstance session. Safe to call on None or already-closed.

    Always use in a try/finally after connect() so sessions do not leak on
    vCenter under concurrent operators.
    """
    if si is None:
        return
    try:
        Disconnect(si)
    except Exception:
        # Session may already be gone; ignore cleanup noise.
        pass


# ---------------------------------------------------------------------------
# Internal inventory helpers (DRY: one DC lookup used by all list_* methods)
# ---------------------------------------------------------------------------

def _content(si):
    """Retrieve the root ServiceContent object (API entry for inventory)."""
    return si.RetrieveContent()


def _find_datacenter(si, datacenter_name: str) -> Optional[Any]:
    """
    Resolve a Datacenter managed object by exact name.

    Returns None if no datacenter matches (caller falls back to manual entry).
    Only top-level rootFolder children that are Datacenter instances are
    considered (standard vCenter inventory layout).
    """
    content = _content(si)
    for entity in content.rootFolder.childEntity:
        # Folders can appear at root in some layouts; only match Datacenter.
        if isinstance(entity, vim.Datacenter) and entity.name == datacenter_name:
            return entity
    return None


def _wait_task(task, timeout_s: int = 120):
    """
    Block until a vSphere Task finishes successfully.

    Busy-waiting without sleep burns CPU; we poll every 0.5s with a deadline
    so hung tasks surface as TimeoutError instead of hanging forever.
    """
    deadline = time.time() + timeout_s
    # Queued = not started yet; running = in progress.
    while task.info.state in (
        vim.TaskInfo.State.running,
        vim.TaskInfo.State.queued,
    ):
        if time.time() > deadline:
            raise TimeoutError(
                f"vSphere task timed out after {timeout_s}s: {task.info}"
            )
        time.sleep(0.5)

    if task.info.state != vim.TaskInfo.State.success:
        # Prefer human message from fault object when present.
        msg = getattr(task.info.error, "msg", None) or str(
            task.info.error or task.info.state
        )
        raise RuntimeError(f"vSphere task failed: {msg}")
    return task.info.result


# ---------------------------------------------------------------------------
# Inventory listing (for interactive menus)
# ---------------------------------------------------------------------------

def list_datacenters(si) -> List[str]:
    """
    Return sorted datacenter names visible to this session.

    Used to populate the first inventory table in `ovbuilder build`.
    """
    content = _content(si)
    names = [
        entity.name
        for entity in content.rootFolder.childEntity
        if isinstance(entity, vim.Datacenter)
    ]
    return sorted(names)


def _wsdl_name(item) -> str:
    """pyVmomi WSDL type, or the Python class name for test doubles."""
    wsdl = getattr(item, "_wsdlName", None)
    if wsdl:
        return str(wsdl)
    cls = type(item)
    return str(getattr(cls, "_wsdlName", None) or cls.__name__)


def _is_cluster_compute(item) -> bool:
    """True only for DRS/HA ClusterComputeResource (not standalone hosts)."""
    if isinstance(item, vim.ClusterComputeResource):
        return True
    return _wsdl_name(item) == "ClusterComputeResource"


def _is_compute_resource(item) -> bool:
    """True for ClusterComputeResource or a bare standalone ComputeResource."""
    if isinstance(item, vim.ComputeResource):
        return True
    return _wsdl_name(item) in {"ComputeResource", "ClusterComputeResource"}


def _is_folder(item) -> bool:
    if isinstance(item, vim.Folder):
        return True
    return _wsdl_name(item) == "Folder"


def _is_host_system(item) -> bool:
    if isinstance(item, vim.HostSystem):
        return True
    return _wsdl_name(item) == "HostSystem"


def _walk_folder(folder, *, depth: int = 0, max_depth: int = 16):
    """Yield hostFolder children, including nested folders."""
    if folder is None or depth > max_depth:
        return
    children = getattr(folder, "childEntity", None) or []
    for item in children:
        yield item
        if _is_folder(item):
            yield from _walk_folder(item, depth=depth + 1, max_depth=max_depth)


def _host_names_from_compute(item) -> List[str]:
    """HostSystem names under a standalone ComputeResource (else the CR name)."""
    names: List[str] = []
    hosts = getattr(item, "host", None) or []
    for host in hosts:
        name = getattr(host, "name", None)
        if name:
            names.append(str(name))
    if names:
        return names
    name = getattr(item, "name", None)
    return [str(name)] if name else []


def list_clusters(si, datacenter_name: str) -> List[str]:
    """
    Return sorted ClusterComputeResource names in the datacenter.

    Standalone ESXi hosts (bare ComputeResource / HostSystem) are never
    included. Terraform ``data.vsphere_compute_cluster`` only accepts a
    real cluster; hosts are listed by ``list_standalone_hosts``.
    """
    dc = _find_datacenter(si, datacenter_name)
    if not dc:
        return []

    names = [
        str(item.name)
        for item in _walk_folder(getattr(dc, "hostFolder", None))
        if _is_cluster_compute(item) and getattr(item, "name", None)
    ]
    return sorted(names)


def list_standalone_hosts(si, datacenter_name: str) -> List[str]:
    """
    Return ESXi host names that are not members of a ClusterComputeResource.

    Used when a datacenter (e.g. SEA3) has no DRS cluster. Terraform then
    places via ``data.vsphere_host`` (compute_type=host).
    """
    dc = _find_datacenter(si, datacenter_name)
    if not dc:
        return []

    names: List[str] = []
    for item in _walk_folder(getattr(dc, "hostFolder", None)):
        if _is_cluster_compute(item):
            continue
        if _is_compute_resource(item):
            names.extend(_host_names_from_compute(item))
        elif _is_host_system(item) and getattr(item, "name", None):
            names.append(str(item.name))
    return sorted(set(names))


def classify_compute(si, datacenter_name: str, name: str) -> str:
    """
    Return ``cluster`` or ``host`` for a live inventory name.

    Unknown names raise RuntimeError. Defaulting to ``cluster`` sent
    standalone host FQDNs through ``vsphere_compute_cluster`` and Terraform
    failed with ``cluster 'esx1…' not found``.
    """
    if not name:
        raise RuntimeError("Compute cluster or standalone ESXi host name is required.")
    clusters = list_clusters(si, datacenter_name)
    if name in clusters:
        return "cluster"
    hosts = list_standalone_hosts(si, datacenter_name)
    if name in hosts:
        return "host"
    if clusters:
        extra = f" Known clusters: {', '.join(clusters)}."
    elif hosts:
        extra = (
            " This datacenter has no ClusterComputeResource; "
            f"standalone hosts: {', '.join(hosts)}."
        )
    else:
        extra = (
            " This datacenter has no ClusterComputeResource and no "
            "standalone ESXi hosts visible to this session."
        )
    raise RuntimeError(
        f"{name!r} is not a vSphere compute cluster or standalone ESXi host "
        f"in datacenter {datacenter_name!r}.{extra}"
    )


def template_exists_in_datacenter(si, vm_name: str, datacenter_name: str) -> bool:
    """True when *vm_name* is a VM template (config.template) in that DC."""
    if not vm_name or not datacenter_name:
        return False
    dc = _find_datacenter(si, datacenter_name)
    if not dc:
        return False
    content = _content(si)
    view = content.viewManager.CreateContainerView(
        dc, [vim.VirtualMachine], True
    )
    try:
        for vm in view.view:
            try:
                if getattr(vm, "name", None) != vm_name:
                    continue
                cfg = getattr(vm, "config", None)
                if cfg is not None and getattr(cfg, "template", False):
                    return True
            except Exception:
                continue
        return False
    finally:
        view.Destroy()


def find_template_datacenters(si, vm_name: str) -> List[str]:
    """Datacenter names that hold an ``ovbuilder-*`` template with this name."""
    if not vm_name:
        return []
    found = [
        str(row["datacenter"])
        for row in list_golden_templates(si)
        if row.get("name") == vm_name and row.get("datacenter")
    ]
    return sorted(set(found))


def require_template(si, vm_name: str, datacenter_name: str) -> None:
    """
    Abort unless *vm_name* is a VM template in *datacenter_name*.

    Called before Terraform so a missing golden is an ovbuilder error, not
    ``vm 'ovbuilder-…' not found`` from ``data.vsphere_virtual_machine``.
    """
    if not vm_name:
        raise RuntimeError(
            "No Packer template selected. Choose an ovbuilder-* golden "
            "before Terraform runs."
        )
    if not datacenter_name:
        raise RuntimeError(
            "No datacenter selected; cannot verify the Packer template "
            "before Terraform."
        )
    if template_exists_in_datacenter(si, vm_name, datacenter_name):
        return
    elsewhere = [
        dc
        for dc in find_template_datacenters(si, vm_name)
        if dc != datacenter_name
    ]
    if elsewhere:
        raise RuntimeError(
            f"Packer template {vm_name!r} was not found in datacenter "
            f"{datacenter_name!r}. Terraform looks up the template in that "
            f"datacenter and would fail. It exists in: {', '.join(elsewhere)}. "
            "Copy the golden into the selected datacenter, or ensure "
            "template_datacenter matches the source DC for a cross-DC clone."
        )
    raise RuntimeError(
        f"Packer template {vm_name!r} was not found in datacenter "
        f"{datacenter_name!r} (and was not found as a VM template in any "
        "other datacenter). Confirm the Packer golden exists as a vSphere "
        f"template named {vm_name} (Mark as Template), then re-run "
        "`ovbuilder build`."
    )


def _datacenter_name_for(entity) -> str:
    """Walk the inventory parent chain until a Datacenter is found."""
    current = entity
    seen = 0
    while current is not None and seen < 32:
        seen += 1
        try:
            if isinstance(current, vim.Datacenter):
                return current.name or ""
            current = getattr(current, "parent", None)
        except Exception:
            return ""
    return ""


def _vm_datastore_name(vm) -> str:
    """Best-effort first datastore name attached to a VM/template."""
    try:
        stores = getattr(vm, "datastore", None) or []
        for store in stores:
            name = getattr(store, "name", None)
            if name:
                return str(name)
    except Exception:
        return ""
    return ""


def list_golden_templates(si) -> List[dict]:
    """
    Return ``ovbuilder-*`` VM templates from every datacenter.

    Each dict is internal (name, datacenter, datastore, guest_id, uuid,
    created, change_version). Callers must not show DC/datastore in pickers.
    Non-templates and names outside the ``ovbuilder-`` prefix are skipped.
    """
    content = _content(si)
    view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True
    )
    found: List[dict] = []
    try:
        for vm in view.view:
            try:
                name = getattr(vm, "name", None) or ""
                if not str(name).startswith("ovbuilder-"):
                    continue
                cfg = getattr(vm, "config", None)
                if cfg is None or not getattr(cfg, "template", False):
                    continue
                created = getattr(cfg, "createDate", None)
                found.append(
                    {
                        "name": str(name),
                        "datacenter": _datacenter_name_for(vm),
                        "datastore": _vm_datastore_name(vm),
                        "guest_id": str(getattr(cfg, "guestId", None) or ""),
                        "uuid": str(getattr(cfg, "uuid", None) or ""),
                        "created": created,
                        "change_version": str(
                            getattr(cfg, "changeVersion", None) or ""
                        ),
                    }
                )
            except Exception:
                # Inaccessible / permission-denied VMs are skipped.
                continue
    finally:
        view.Destroy()
    return found


def list_datastores(si, datacenter_name: str) -> List[str]:
    """Return sorted datastore names attached to the datacenter."""
    dc = _find_datacenter(si, datacenter_name)
    if not dc:
        return []
    # dc.datastore is a list of Datastore MORs already scoped to this DC.
    return sorted(ds.name for ds in dc.datastore)


def list_datastore_clusters(si, datacenter_name: str) -> List[str]:
    """
    Return Storage DRS datastore-cluster (StoragePod) names in this DC.

    YAVIN-DEV / YAVIN-PROD (ATLC) and HOTH_DEV / HOTH_PROD (PDXC) are
    this type. Placing a VM on the cluster lets SDRS pick a member
    datastore; operators do not pick individual LUNs.
    """
    dc = _find_datacenter(si, datacenter_name)
    if not dc:
        return []
    content = _content(si)
    view = content.viewManager.CreateContainerView(
        dc, [vim.StoragePod], True
    )
    try:
        return sorted(pod.name for pod in view.view)
    finally:
        view.Destroy()


def list_networks(si, datacenter_name: str) -> List[str]:
    """
    Return sorted network / port-group names visible on the datacenter.

    Includes standard port groups and distributed port groups as exposed
    on dc.network (vCenter aggregates both).
    """
    dc = _find_datacenter(si, datacenter_name)
    if not dc:
        return []
    return sorted(net.name for net in dc.network)


def list_isos(si, datastore_name: str, datacenter_name: str) -> List[str]:
    """
    List .iso files under a datastore (paths relative to datastore root).

    Implementation notes
    --------------------
    * Uses DatastoreBrowser.SearchDatastoreSubFolders_Task (recursive).
    * Filters with IsoImageQuery so non-ISO files are not returned.
    * Strips the leading ``[datastore] `` folderPath prefix so Terraform
      and menus get clean paths like ``isos/AlmaLinux-10.iso``.
    * Returns [] on any browser failure so the CLI can fall back to manual
      path entry (never crashes the interactive flow).
    """
    dc = _find_datacenter(si, datacenter_name)
    if not dc:
        return []

    # Locate the named datastore among those mounted on this DC.
    ds = None
    for store in dc.datastore:
        if store.name == datastore_name:
            ds = store
            break
    if not ds or not ds.browser:
        # No browser = storage that cannot be searched (or permissions).
        return []

    # Restrict search to ISO image file types only.
    search_spec = vim.host.DatastoreBrowser.SearchSpec()
    search_spec.query = [vim.host.DatastoreBrowser.IsoImageQuery()]
    search_spec.details = vim.host.DatastoreBrowser.FileInfo.Details(
        fileType=True, fileSize=True
    )
    search_spec.sortFoldersFirst = True

    # Search root of this datastore: "[name]"
    datastore_path = f"[{datastore_name}]"

    try:
        task = ds.browser.SearchDatastoreSubFolders_Task(
            datastore_path, search_spec
        )
        # Reuse shared waiter (sleep-based; no busy-spin).
        _wait_task(task, timeout_s=180)

        results: List[str] = []
        for res in task.info.result or []:
            for f in res.file or []:
                if not (f.path and f.path.lower().endswith(".iso")):
                    continue
                fp = res.folderPath or ""
                # folderPath looks like "[isos] subdir/" — strip [name] prefix.
                if fp.startswith("["):
                    close = fp.find("]")
                    if close != -1:
                        fp = fp[close + 1 :]
                path = (fp + (f.path or "")).strip().lstrip("/")
                if path:
                    results.append(path)
        # set() removes duplicates if browser returns overlapping folders.
        return sorted(set(results))
    except Exception:
        # Discovery is best-effort; caller offers manual ISO entry.
        return []


# ---------------------------------------------------------------------------
# VM lookup + install-media disconnect (ISO lock fix)
# ---------------------------------------------------------------------------

def find_vm_by_name(si, vm_name: str):
    """
    Find a VirtualMachine by exact inventory name (any folder).

    Uses a recursive ContainerView so VMs in nested folders are found.
    Returns the first match or None.
    """
    content = _content(si)
    view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True
    )
    try:
        for vm in view.view:
            if vm.name == vm_name:
                return vm
    finally:
        # Always destroy container views to avoid server-side leaks.
        view.Destroy()
    return None


def disconnect_install_media(si, vm_name: str) -> dict:
    """
    Detach datastore ISO(s) from all CD/DVD devices; leave client devices.

    Problem this solves
    -------------------
    Terraform ISO mode attaches install media for first boot. Leaving that
    attachment after install:

      1. Locks the ISO file on the datastore (other builds fail).
      2. Can re-enter the installer on the next guest reboot.

    Packer goldens use remove_cdrom=true; this is still run as hygiene after
    clone in case a template retained a CD device.

    Algorithm
    ---------
    For each VirtualCdrom on the VM:
      * Change backing to RemotePassthroughBackingInfo (empty client device).
      * Set connectable.connected / startConnected = False.
    Prefer hard disk in bootOptions when possible.

    Returns
    -------
    dict with keys: changed (bool), devices (int), detail (str)
    """
    vm = find_vm_by_name(si, vm_name)
    if vm is None:
        raise RuntimeError(f"VM not found in vCenter: {vm_name}")

    device_changes = []
    for device in vm.config.hardware.device:
        # Skip non-CD devices (NICs, disks, controllers, etc.).
        if not isinstance(device, vim.vm.device.VirtualCdrom):
            continue

        # Empty client-device backing = no datastore file held open.
        backing = vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo()
        backing.deviceName = ""
        backing.exclusive = False
        device.backing = backing

        # Ensure firmware/guest does not try to use the empty drive at boot.
        connectable = vim.vm.device.VirtualDevice.ConnectInfo()
        connectable.connected = False
        connectable.startConnected = False
        connectable.allowGuestControl = True
        device.connectable = connectable

        # EDIT (not add/remove) preserves the virtual CD hardware slot.
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

    # Prefer disk boot so EFI/BIOS does not sit on empty CD.
    try:
        boot = vim.vm.BootOptions()
        disk_keys = [
            d.key
            for d in vm.config.hardware.device
            if isinstance(d, vim.vm.device.VirtualDisk)
        ]
        if disk_keys:
            boot.bootOrder = [
                vim.vm.BootOptions.BootableDiskDevice(deviceKey=disk_keys[0])
            ]
            cfg.bootOptions = boot
    except Exception:
        # Boot order is nice-to-have; media disconnect is the critical part.
        pass

    task = vm.ReconfigVM_Task(spec=cfg)
    _wait_task(task)

    return {
        "changed": True,
        "devices": len(device_changes),
        "detail": (
            f"Disconnected install media on {len(device_changes)} CD/DVD device(s)"
        ),
    }
