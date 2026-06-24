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
            folder = res.folderPath or ""
            for f in res.file or []:
                if f.path and f.path.lower().endswith(".iso"):
                    # Build relative path
                    path = (folder + f.path).lstrip("/")
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
