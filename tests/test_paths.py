"""Unit tests for platform-aware config/data paths."""

from __future__ import annotations

from ovbuilder import paths


def test_config_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert paths.config_dir() == tmp_path / "cfg" / "ovbuilder"


def test_data_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert paths.data_dir() == tmp_path / "data" / "ovbuilder"


def test_secrets_dir_matches_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from ovbuilder.secrets import secrets_dir, secrets_env_path

    assert secrets_dir() == tmp_path / "cfg" / "ovbuilder"
    assert secrets_env_path() == tmp_path / "cfg" / "ovbuilder" / "secrets.env"


def test_golden_help_mentions_both_platforms(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from ovbuilder.secrets import golden_password_setup_help

    help_text = golden_password_setup_help()
    assert "OVBUILDER_GOLDEN_PASSWORD" in help_text
    assert "docs/SECRETS.md" in help_text
    assert "export" in help_text or "$env:" in help_text


def test_allow_password_ssh_default_false(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("OVBUILDER_ALLOW_PASSWORD_SSH", raising=False)
    from ovbuilder.secrets import allow_password_ssh

    assert allow_password_ssh() is False


def test_allow_password_ssh_env_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("OVBUILDER_ALLOW_PASSWORD_SSH", "true")
    from ovbuilder.secrets import allow_password_ssh

    assert allow_password_ssh() is True


def test_authorized_keys_file_default_path(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("OVBUILDER_SSH_AUTHORIZED_KEYS_FILE", raising=False)
    from ovbuilder.secrets import ssh_authorized_keys_file

    assert ssh_authorized_keys_file() == tmp_path / "cfg" / "ovbuilder" / "authorized_keys"
