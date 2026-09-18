"""vSphere password resolution from env / secrets.env (never logged)."""

from __future__ import annotations

from ovbuilder.secrets import get_vsphere_password

from placeholders import placeholder_value


def test_explicit_password_wins(monkeypatch):
    from_env = f"{placeholder_value()}-env"
    from_flag = f"{placeholder_value()}-flag"
    monkeypatch.setenv("VSPHERE_PASSWORD", from_env)
    assert get_vsphere_password(from_flag) == from_flag


def test_env_order(monkeypatch):
    monkeypatch.delenv("OVBUILDER_VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("TF_VAR_vsphere_password", raising=False)
    from_env = f"{placeholder_value()}-env"
    monkeypatch.setenv("VSPHERE_PASSWORD", from_env)
    assert get_vsphere_password(None) == from_env


def test_secrets_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OVBUILDER_VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("TF_VAR_vsphere_password", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    secrets_dir = tmp_path / "cfg" / "ovbuilder"
    secrets_dir.mkdir(parents=True)
    from_file = f"{placeholder_value()}-file"
    (secrets_dir / "secrets.env").write_text(
        f"VSPHERE_PASSWORD={from_file}\n", encoding="utf-8"
    )
    assert get_vsphere_password(None) == from_file


def test_missing_password(monkeypatch, tmp_path):
    monkeypatch.delenv("OVBUILDER_VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("VSPHERE_PASSWORD", raising=False)
    monkeypatch.delenv("TF_VAR_vsphere_password", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert get_vsphere_password(None) is None
