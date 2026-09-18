"""Security hardening: secrets redaction, SECRET_KEY, LDAP TLS."""

from __future__ import annotations

import ssl
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.auth import _ldap_connection, _ldap_server, _ldap_tls
from app.config import Settings, get_settings
from app.models import (
    BuildJob,
    BuildJobPublic,
    BuildRequest,
    BuildStatus,
    redact_build_request,
)
from app.tasks import _build_command
from tests.placeholders import placeholder_value


def _settings(**kwargs) -> Settings:
    defaults = {
        "secret_key": "test-secret-key-that-is-long-enough-32ch",
        "debug": False,
        "_env_file": None,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def test_secret_key_missing_fails_outside_debug(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DEBUG", "false")
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(secret_key="", debug=False, _env_file=None)


@pytest.mark.parametrize(
    "value",
    ["change-me-in-production", "change-me-to-a-long-random-string"],
)
def test_secret_key_example_fails_outside_debug(monkeypatch, value):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DEBUG", "false")
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(secret_key=value, debug=False, _env_file=None)


def test_secret_key_short_always_fails(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(secret_key="too-short-to-be-safe", debug=True, _env_file=None)


def test_secret_key_ephemeral_when_debug(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    settings = Settings(secret_key="", debug=True, _env_file=None)
    assert len(settings.secret_key) >= 32
    assert settings.secret_key not in (
        "",
        "change-me-in-production",
        "change-me-to-a-long-random-string",
    )


def test_public_job_omits_vsphere_password():
    req = BuildRequest(
        hostname="web01",
        ip="10.0.0.10",
        os_image="ubuntu-24.04",
        vsphere_password=placeholder_value(),
        vsphere_user="svc@example.com",
    )
    job = BuildJob(
        id="job-1",
        status=BuildStatus.queued,
        request=req,
        requested_by="builder",
        created_at=datetime.now(timezone.utc),
    )
    public = BuildJobPublic.from_job(job)
    dumped = public.model_dump()
    assert "vsphere_password" not in dumped["request"]
    assert "vsphere_password" not in public.model_dump_json()
    schema = BuildJobPublic.model_json_schema()
    request_props = schema["$defs"]["BuildRequestPublic"]["properties"]
    assert "vsphere_password" not in request_props


def test_redact_build_request_clears_password():
    guest = placeholder_value()
    req = BuildRequest(
        hostname="web01",
        ip="10.0.0.10",
        os_image="ubuntu-24.04",
        vsphere_password=guest,
    )
    redacted = redact_build_request(req)
    assert redacted.vsphere_password is None
    assert req.vsphere_password == guest


def test_build_command_uses_env_not_argv():
    get_settings.cache_clear()
    guest = placeholder_value()
    payload = {
        "hostname": "web01",
        "ip": "10.0.0.10",
        "os_image": "ubuntu-24.04",
        "vsphere_server": "vcenter.example.com",
        "vsphere_user": "svc@example.com",
    }
    payload["vsphere_password"] = guest
    cmd, env = _build_command(payload, "job-1")
    assert "--vsphere-password" not in cmd
    assert guest not in cmd
    assert env["VSPHERE_PASSWORD"] == guest
    assert env["TF_VAR_vsphere_password"] == guest
    assert env["OVBUILDER_VSPHERE_PASSWORD"] == guest


def test_ldap_tls_verifies_by_default(tmp_path):
    ca = tmp_path / "example-ldap-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    settings = _settings(
        ldap_server_url="ldaps://ldap.example.com:636",
        ldap_use_ssl=True,
        ldap_ssl_verify=True,
        ldap_ca_certs_file=str(ca),
    )
    tls = _ldap_tls(settings)
    assert tls is not None
    assert tls.validate == ssl.CERT_REQUIRED
    assert tls.ca_certs_file == str(ca)
    server = _ldap_server(settings)
    assert server.ssl is True


def test_ldap_ca_missing_file_fails_fast(tmp_path):
    missing = tmp_path / "missing-ca.pem"
    settings = _settings(
        ldap_server_url="ldaps://ldap.example.com:636",
        ldap_ssl_verify=True,
        ldap_ca_certs_file=str(missing),
    )
    with pytest.raises(FileNotFoundError, match="LDAP_CA_CERTS_FILE"):
        _ldap_tls(settings)


def test_ldap_tls_insecure_opt_in():
    settings = _settings(
        ldap_server_url="ldaps://ldap.example.com:636",
        ldap_ssl_verify=False,
    )
    tls = _ldap_tls(settings)
    assert tls is not None
    assert tls.validate == ssl.CERT_NONE


def test_start_tls_called_for_ldap_starttls():
    settings = _settings(
        ldap_server_url="ldap://ldap.example.com:389",
        ldap_use_ssl=False,
        ldap_use_starttls=True,
        ldap_ssl_verify=True,
    )
    server = _ldap_server(settings)
    conn = MagicMock()
    conn.open.return_value = True
    conn.start_tls.return_value = True
    conn.bind.return_value = True

    with patch("app.auth.Connection", return_value=conn) as ctor:
        opened = _ldap_connection(server, settings, "cn=svc", placeholder_value())

    ctor.assert_called_once()
    assert ctor.call_args.kwargs["auto_bind"] is False
    conn.open.assert_called_once()
    conn.start_tls.assert_called_once()
    conn.bind.assert_called_once()
    assert opened is conn


def test_start_tls_skipped_on_ldaps():
    settings = _settings(
        ldap_server_url="ldaps://ldap.example.com:636",
        ldap_use_ssl=True,
        ldap_use_starttls=True,
    )
    server = _ldap_server(settings)
    conn = MagicMock()
    conn.open.return_value = True
    conn.bind.return_value = True

    with patch("app.auth.Connection", return_value=conn):
        _ldap_connection(server, settings, "cn=svc", placeholder_value())

    conn.start_tls.assert_not_called()
    conn.bind.assert_called_once()
