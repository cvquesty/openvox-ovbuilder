"""vSphere golden scan + compute classification (no live vCenter)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from ovbuilder import vsphere


def _vm(*, name: str, template: bool, guest_id: str = "ubuntu64Guest"):
    vm = MagicMock()
    vm.name = name
    vm.config.template = template
    vm.config.guestId = guest_id
    vm.config.uuid = "uuid-" + name
    vm.config.createDate = datetime(2026, 1, 2)
    vm.config.changeVersion = "2026-01-02T00:00:00"
    vm.datastore = [MagicMock(name="ds")]
    vm.datastore[0].name = "HOTH_DEV"
    return vm


def test_list_golden_templates_scans_all_dcs_and_filters():
    keep = _vm(name="ovbuilder-ubuntu-24.04", template=True)
    skip_name = _vm(name="web-01", template=True)
    skip_not_template = _vm(name="ovbuilder-ubuntu-24.04-clone", template=False)
    view = MagicMock()
    view.view = [keep, skip_name, skip_not_template]
    content = MagicMock()
    content.viewManager.CreateContainerView.return_value = view
    si = MagicMock()

    with patch.object(vsphere, "_content", return_value=content), patch.object(
        vsphere, "_datacenter_name_for", return_value="PDXC"
    ):
        rows = vsphere.list_golden_templates(si)

    view.Destroy.assert_called_once()
    assert len(rows) == 1
    assert rows[0]["name"] == "ovbuilder-ubuntu-24.04"
    assert rows[0]["datacenter"] == "PDXC"
    assert rows[0]["guest_id"] == "ubuntu64Guest"
    assert rows[0]["datastore"] == "HOTH_DEV"


def test_list_golden_templates_skips_inaccessible_vms():
    bad = MagicMock()
    type(bad).name = property(lambda _self: (_ for _ in ()).throw(RuntimeError("denied")))
    good = _vm(name="ovbuilder-almalinux-10", template=True, guest_id="other4xLinux64Guest")
    view = MagicMock()
    view.view = [bad, good]
    content = MagicMock()
    content.viewManager.CreateContainerView.return_value = view
    with patch.object(vsphere, "_content", return_value=content), patch.object(
        vsphere, "_datacenter_name_for", return_value="PDXC"
    ):
        rows = vsphere.list_golden_templates(MagicMock())
    assert [r["name"] for r in rows] == ["ovbuilder-almalinux-10"]


def test_classify_compute_defaults_unknown_to_cluster():
    with patch.object(vsphere, "_find_datacenter", return_value=None):
        assert vsphere.classify_compute(object(), "SEA3", "esx1") == "cluster"
    assert vsphere.classify_compute(object(), "SEA3", "") == "cluster"
