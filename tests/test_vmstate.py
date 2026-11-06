"""Lifecycle actions vs per-VM Terraform state and the hostname lock (issue #5)."""

from __future__ import annotations

import json
import stat
from unittest.mock import patch

import pytest

from ovbuilder import vmops, vmstate
from ovbuilder.terraform import state_dir_for_vm


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "data" / "ovbuilder"


def _seed_state(vm_name: str, uuid: str = "4210abcd-0000"):
    sdir = state_dir_for_vm(vm_name)
    state = {
        "resources": [
            {
                "type": "vsphere_virtual_machine",
                "instances": [{"attributes": {"name": vm_name, "uuid": uuid.upper(), "id": uuid}}],
            }
        ]
    }
    (sdir / "terraform.tfstate").write_text(json.dumps(state))
    (sdir / "ovbuilder.auto.tfvars.json").write_text('{"vsphere_password": "x"}')
    (sdir / "module").mkdir()
    (sdir / "module" / "main.tf").write_text("# copy")
    (sdir / ".terraform").mkdir()
    return sdir


def test_existing_state_dir_never_creates(data_home):
    assert vmstate.existing_state_dir("web01") is None
    assert not (data_home / "tfstate" / "web01").exists()


def test_state_vm_uuids_reads_local_state(data_home):
    _seed_state("web01", "4210ABCD-0000")
    assert vmstate.state_vm_uuids("web01") == {"4210abcd-0000"}
    assert vmstate.state_vm_uuids("other") == set()


def test_retire_state_keeps_state_drops_secrets(data_home):
    sdir = _seed_state("web01")
    retired = vmstate.retire_state(sdir)
    assert retired is not None and retired.parent == data_home / "tfstate" / ".retired"
    assert (retired / "terraform.tfstate").is_file()
    assert not (retired / "ovbuilder.auto.tfvars.json").exists()
    assert stat.S_IMODE(retired.stat().st_mode) == 0o700
    remaining = {p.name for p in sdir.iterdir()}
    assert remaining <= {vmstate.LOCK_FILE}


def test_lock_excludes_a_concurrent_holder(data_home):
    sdir = state_dir_for_vm("web01")
    with vmstate.hostname_lock(sdir):
        with pytest.raises(vmstate.StateLockedError):
            with vmstate.vm_lock("web01"):
                pass
    with vmstate.vm_lock("web01") as held:
        assert held == sdir


def test_vm_lock_without_state_and_create_false_is_a_noop(data_home):
    with vmstate.vm_lock("never-built", create=False) as held:
        assert held is None
    assert vmstate.existing_state_dir("never-built") is None


def test_destroy_with_state_destroys_and_retires(data_home):
    sdir = _seed_state("web01")
    with patch.object(vmops, "vm_exists", return_value=True), patch.object(
        vmops, "destroy_vm", return_value={"name": "web01", "changed": True, "action": "destroyed"}
    ) as destroy:
        result = vmstate.destroy_with_state(object(), "web01")
    destroy.assert_called_once()
    assert result["action"] == "destroyed"
    assert result["state_retired_to"]
    assert not (sdir / "terraform.tfstate").exists()


def test_destroy_refused_while_build_holds_lock(data_home):
    sdir = _seed_state("web01")
    with patch.object(vmops, "vm_exists", return_value=True), patch.object(vmops, "destroy_vm") as destroy:
        with vmstate.hostname_lock(sdir):
            with pytest.raises(vmstate.StateLockedError):
                vmstate.destroy_with_state(object(), "web01")
    destroy.assert_not_called()
    assert (sdir / "terraform.tfstate").exists()


def test_destroy_cleans_leftover_state_when_vm_absent(data_home):
    sdir = _seed_state("web01")
    with patch.object(vmops, "vm_exists", return_value=False), patch.object(vmops, "destroy_vm") as destroy:
        result = vmstate.destroy_with_state(object(), "web01")
    destroy.assert_not_called()
    assert result["action"] == "already_absent"
    assert not (sdir / "terraform.tfstate").exists()


def test_destroy_unknown_vm_without_state_is_not_found(data_home):
    with patch.object(vmops, "vm_exists", return_value=False):
        with pytest.raises(vmops.VmNotFoundError):
            vmstate.destroy_with_state(object(), "ghost")


def test_keep_state(data_home):
    sdir = _seed_state("web01")
    with patch.object(vmops, "vm_exists", return_value=True), patch.object(
        vmops, "destroy_vm", return_value={"name": "web01", "changed": True}
    ):
        vmstate.destroy_with_state(object(), "web01", keep_state=True)
    assert (sdir / "terraform.tfstate").exists()
