"""Per-VM working copies, hostname locks, and remote backends (issue #8)."""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ovbuilder.config import OvbuilderConfig
from ovbuilder.terraform import (
    BACKEND_OVERRIDE_FILE,
    StateLockedError,
    hostname_lock,
    run_terraform_apply,
    state_dir_for_vm,
)


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "data" / "ovbuilder"


@pytest.fixture
def tf_dir(tmp_path):
    root = tmp_path / "terraform"
    (root / "modules" / "vm").mkdir(parents=True)
    (root / "main.tf").write_text('module "vm" { source = "./modules/vm" }\n')
    (root / "versions.tf").write_text('terraform {\n  backend "local" {}\n}\n')
    (root / ".terraform.lock.hcl").write_text("# lock\n")
    (root / "modules" / "vm" / "main.tf").write_text("# vm\n")
    # Must never be copied into a working copy.
    (root / "terraform.tfstate.legacy-shared").write_text("{}")
    (root / ".terraform").mkdir()
    return root


class _Recorder:
    """Stands in for subprocess.run; optionally holds apply open."""

    def __init__(self, apply_delay: float = 0.0):
        self.calls: list[dict] = []
        self.apply_delay = apply_delay
        self._lock = threading.Lock()

    def __call__(self, cmd, cwd=None, env=None, check=False):
        with self._lock:
            self.calls.append(
                {"cmd": list(cmd), "cwd": Path(cwd), "tf_data": env.get("TF_DATA_DIR")}
            )
        if cmd[1] == "apply" and self.apply_delay:
            time.sleep(self.apply_delay)

    def of(self, verb: str) -> list[dict]:
        return [c for c in self.calls if c["cmd"][1] == verb]


def _vars(name: str) -> dict:
    return {"vm_name": name, "template_name": "ovbuilder-ubuntu-24.04"}


def test_parallel_builds_run_in_isolated_workdirs(data_home, tf_dir):
    rec = _Recorder(apply_delay=0.2)
    results: dict[str, bool] = {}

    def build(name: str) -> None:
        results[name] = run_terraform_apply(tf_dir, _vars(name), OvbuilderConfig())

    with patch("ovbuilder.terraform.subprocess.run", side_effect=rec):
        threads = [threading.Thread(target=build, args=(n,)) for n in ("web01", "web02")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert results == {"web01": True, "web02": True}
    applies = rec.of("apply")
    assert len(applies) == 2
    cwds = {c["cwd"] for c in applies}
    assert cwds == {
        data_home / "tfstate" / "web01" / "module",
        data_home / "tfstate" / "web02" / "module",
    }
    assert {c["tf_data"] for c in applies} == {
        str(data_home / "tfstate" / "web01" / ".terraform"),
        str(data_home / "tfstate" / "web02" / ".terraform"),
    }
    # Nothing ran in the shared module directory.
    assert all(c["cwd"] != tf_dir for c in rec.calls)
    for name in ("web01", "web02"):
        work = data_home / "tfstate" / name / "module"
        assert (work / "main.tf").is_file()
        assert (work / "modules" / "vm" / "main.tf").is_file()
        assert (work / ".terraform.lock.hcl").is_file()
        assert not (work / ".terraform").exists()
        assert not (work / "terraform.tfstate.legacy-shared").exists()
    inits = rec.of("init")
    assert sorted(c["cmd"][-1] for c in inits) == sorted(
        f"-backend-config=path={data_home / 'tfstate' / n / 'terraform.tfstate'}"
        for n in ("web01", "web02")
    )


def test_same_hostname_build_fails_fast_while_locked(data_home, tf_dir):
    rec = _Recorder()
    sdir = state_dir_for_vm("web01")
    with patch("ovbuilder.terraform.subprocess.run", side_effect=rec):
        with hostname_lock(sdir):
            assert run_terraform_apply(tf_dir, _vars("web01"), OvbuilderConfig()) is False
        assert rec.calls == []
        # Lock is released afterwards; the next build proceeds.
        assert run_terraform_apply(tf_dir, _vars("web01"), OvbuilderConfig()) is True
    assert len(rec.of("apply")) == 1


def test_hostname_lock_is_exclusive(data_home):
    sdir = state_dir_for_vm("web01")
    with hostname_lock(sdir):
        with pytest.raises(StateLockedError):
            with hostname_lock(sdir):
                pass
    with hostname_lock(sdir):
        pass


def test_apply_passes_terraform_lock_timeout(data_home, tf_dir):
    rec = _Recorder()
    cfg = OvbuilderConfig(terraform_lock_timeout="5m")
    with patch("ovbuilder.terraform.subprocess.run", side_effect=rec):
        assert run_terraform_apply(tf_dir, _vars("web01"), cfg) is True
    cmd = rec.of("apply")[0]["cmd"]
    assert "-lock=true" in cmd
    assert "-lock-timeout=5m" in cmd


def test_s3_backend_gets_per_vm_key_off_argv(data_home, tf_dir):
    rec = _Recorder()
    cfg = OvbuilderConfig(
        terraform_backend="s3",
        terraform_backend_config={
            "bucket": "tf-state",
            "region": "us-east-1",
            "use_lockfile": True,
        },
        terraform_state_prefix="ovbuilder/prod",
    )
    with patch("ovbuilder.terraform.subprocess.run", side_effect=rec):
        assert run_terraform_apply(tf_dir, _vars("web01.example.com"), cfg) is True

    sdir = data_home / "tfstate" / "web01.example.com"
    override = (sdir / "module" / BACKEND_OVERRIDE_FILE).read_text()
    assert 'backend "s3" {}' in override
    assert not (tf_dir / BACKEND_OVERRIDE_FILE).exists()

    backend_hcl = sdir / "backend.hcl"
    text = backend_hcl.read_text()
    assert 'bucket = "tf-state"' in text
    assert "use_lockfile = true" in text
    assert 'key = "ovbuilder/prod/web01.example.com/terraform.tfstate"' in text
    if os.name == "posix":
        assert stat.S_IMODE(backend_hcl.stat().st_mode) == 0o600

    init_cmd = rec.of("init")[0]["cmd"]
    assert f"-backend-config={backend_hcl}" in init_cmd
    assert "-reconfigure" in init_cmd
    assert not any("tf-state" in part for part in init_cmd)
    assert rec.of("state") == []


def test_remote_backend_pushes_existing_local_state_once(data_home, tf_dir):
    rec = _Recorder()
    sdir = state_dir_for_vm("web01")
    (sdir / "terraform.tfstate").write_text('{"version": 4}')
    cfg = OvbuilderConfig(terraform_backend="consul")
    with patch("ovbuilder.terraform.subprocess.run", side_effect=rec):
        assert run_terraform_apply(tf_dir, _vars("web01"), cfg) is True
        assert run_terraform_apply(tf_dir, _vars("web01"), cfg) is True

    pushes = rec.of("state")
    assert len(pushes) == 1
    assert pushes[0]["cmd"][:3] == ["terraform", "state", "push"]
    assert "-force" not in pushes[0]["cmd"]
    assert not (sdir / "terraform.tfstate").exists()
    assert (sdir / "terraform.tfstate.migrated").exists()
    verbs = [c["cmd"][1] for c in rec.calls]
    assert verbs.index("state") < verbs.index("apply")


def test_unsupported_backend_is_rejected(data_home, tf_dir):
    rec = _Recorder()
    cfg = OvbuilderConfig(terraform_backend="http")
    with patch("ovbuilder.terraform.subprocess.run", side_effect=rec):
        assert run_terraform_apply(tf_dir, _vars("web01"), cfg) is False
    assert rec.calls == []
