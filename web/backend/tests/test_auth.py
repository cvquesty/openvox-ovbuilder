"""OAuth2 login contract: form-urlencoded success/failure, JWT /me, roles."""

from __future__ import annotations

from app.auth import create_token, decode_token, role_from_groups
from app.config import Settings, get_settings
from app.models import Role

from tests.conftest import auth_header


def _ldap_ok(username: str, groups: list[str]):
    def _auth(_settings, user: str, password: str):
        if user == username and password == "correct-horse":
            return {"username": username, "groups": groups}
        return None

    return _auth


async def test_login_success_form_urlencoded(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.auth.ldap_authenticate",
        _ldap_ok("alice", ["ovbuilder-builders"]),
    )
    res = await auth_client.post(
        "/api/auth/login",
        data={"username": "alice", "password": "correct-horse"},
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


async def test_login_rejects_bad_credentials(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.auth.ldap_authenticate",
        _ldap_ok("alice", ["ovbuilder-builders"]),
    )
    res = await auth_client.post(
        "/api/auth/login",
        data={"username": "alice", "password": "wrong"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Bad credentials"


async def test_login_rejects_json_body(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.auth.ldap_authenticate",
        _ldap_ok("alice", ["ovbuilder-builders"]),
    )
    res = await auth_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "correct-horse"},
    )
    assert res.status_code == 422


async def test_login_ldap_disabled_is_unavailable(auth_client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ldap_enabled", False)
    res = await auth_client.post(
        "/api/auth/login",
        data={"username": "alice", "password": "correct-horse"},
    )
    assert res.status_code == 503
    assert res.json()["detail"] == "LDAP disabled"


async def test_me_with_valid_token(auth_client):
    res = await auth_client.get("/api/auth/me", headers=auth_header("alice", Role.builder))
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == "alice"
    assert body["role"] == "builder"


async def test_me_without_token_is_unauthorized(auth_client):
    res = await auth_client.get("/api/auth/me")
    assert res.status_code == 401


async def test_login_then_me_roundtrip(auth_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.auth.ldap_authenticate",
        _ldap_ok("root", ["ovbuilder-admins"]),
    )
    login = await auth_client.post(
        "/api/auth/login",
        data={"username": "root", "password": "correct-horse"},
    )
    token = login.json()["access_token"]
    me = await auth_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json() == {"username": "root", "role": "admin", "display_name": None, "email": None}


async def test_roles_is_public(auth_client):
    res = await auth_client.get("/api/auth/roles")
    assert res.status_code == 200
    assert res.json() == ["admin", "builder", "viewer"]


async def test_health(auth_client):
    res = await auth_client.get("/api/health")
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
