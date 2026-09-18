"""OAuth2 login contract: form-urlencoded success/failure, JWT /me, roles."""

from __future__ import annotations

from app.auth import create_token, decode_token, role_from_groups
from app.config import Settings, get_settings
from app.models import Role

from tests.conftest import auth_header
from tests.placeholders import login_form, placeholder_value


def _ldap_ok(username: str, groups: list[str]):
    expected = placeholder_value()

    def _auth(_settings, user: str, supplied: str):
        if user == username and supplied == expected:
            return {"username": username, "groups": groups}
        return None

    return _auth


def test_login_success_form_urlencoded(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.auth.ldap_authenticate",
        _ldap_ok("alice", ["ovbuilder-builders"]),
    )
    res = auth_client.post(
        "/api/auth/login",
        data=login_form("alice"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["username"] == "alice"
    assert body["role"] == "builder"
    assert body["access_token"]
    payload = decode_token(body["access_token"], get_settings())
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["role"] == "builder"


def test_login_rejects_bad_credentials(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.auth.ldap_authenticate",
        _ldap_ok("alice", ["ovbuilder-builders"]),
    )
    res = auth_client.post(
        "/api/auth/login",
        data=login_form("alice", match=False),
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Bad credentials"


def test_login_rejects_json_body(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.auth.ldap_authenticate",
        _ldap_ok("alice", ["ovbuilder-builders"]),
    )
    res = auth_client.post(
        "/api/auth/login",
        json=login_form("alice"),
    )
    assert res.status_code == 422


def test_login_ldap_disabled_is_unavailable(auth_client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ldap_enabled", False)
    res = auth_client.post(
        "/api/auth/login",
        data=login_form("alice"),
    )
    assert res.status_code == 503
    assert res.json()["detail"] == "LDAP disabled"


def test_me_with_valid_token(auth_client):
    res = auth_client.get("/api/auth/me", headers=auth_header("alice", Role.builder))
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == "alice"
    assert body["role"] == "builder"


def test_me_without_token_is_unauthorized(auth_client):
    res = auth_client.get("/api/auth/me")
    assert res.status_code == 401


def test_login_then_me_roundtrip(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.auth.ldap_authenticate",
        _ldap_ok("root", ["ovbuilder-admins"]),
    )
    login = auth_client.post(
        "/api/auth/login",
        data=login_form("root"),
    )
    token = login.json()["access_token"]
    me = auth_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json() == {
        "username": "root",
        "role": "admin",
        "display_name": None,
        "email": None,
    }


def test_roles_is_public(auth_client):
    res = auth_client.get("/api/auth/roles")
    assert res.status_code == 200
    assert res.json() == ["admin", "builder", "viewer"]


def test_health(auth_client):
    res = auth_client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_role_from_groups_precedence():
    settings = Settings(
        secret_key="test-secret-key-that-is-long-enough-32ch",
        _env_file=None,
    )
    assert role_from_groups(settings, ["ovbuilder-admins", "ovbuilder-builders"]) == Role.admin
    assert role_from_groups(settings, ["OVBUILDER-BUILDERS"]) == Role.builder
    assert role_from_groups(settings, ["ovbuilder-viewers"]) == Role.viewer
    assert role_from_groups(settings, ["unrelated"]) == Role.viewer


def test_create_and_decode_token():
    settings = get_settings()
    token = create_token("alice", Role.builder, settings)
    payload = decode_token(token, settings)
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["role"] == "builder"
    assert decode_token("not-a-jwt", settings) is None
