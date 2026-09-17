"""vSphere password resolution from env / secrets.env (never logged)."""

from __future__ import annotations

from ovbuilder.secrets import get_vsphere_password


def test_explicit_password_wins(monkeypatch):
    monkeypatch.setenv("VSPHERE_PASSWORD", "from-env")
    assert get_vsphere_password("from-flag") == "from-flag"


def test_env_order(monkeypatch):
    monkeypatch.delenv("OVBUILDER_VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("TF_VAR_vsphere_password", raising=False)
    monkeypatch.setenv("VSPHERE_PASSWORD", "vsphere-env")
    assert get_vsphere_password(None) == "vsphere-env"


def test_secrets_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OVBUILDER_VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("TF_VAR_vsphere_password", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    secrets_dir = tmp_path / "cfg" / "ovbuilder"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "secrets.env").write_text(
        "VSPHERE_PASSWORD=from-file\n", encoding="utf-8"
    )
    assert get_vsphere_password(None) == "from-file"


def test_missing_password(monkeypatch, tmp_path):
    monkeypatch.delenv("OVBUILDER_VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("TF_VAR_vsphere_password", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert get_vsphere_password(None) is None
