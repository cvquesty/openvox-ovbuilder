"""Role freshness: local overrides beat LDAP; directory changes apply before JWT expiry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import (
    LdapUnavailable,
    create_token,
    decode_token,
    get_current_user,
    resolve_user_role,
    role_from_groups,
)
from app.config import Settings, get_settings
from app.database import get_user, set_role_override, upsert_user
from app.models import Role, coerce_role, effective_role

from tests.conftest import auth_header


def _settings(**kwargs) -> Settings:
    return Settings(
        secret_key="test-secret-key-that-is-long-enough-32ch",
        _env_file=None,
        **kwargs,
    )


def test_effective_role_override_beats_ldap_mapping():
    assert effective_role(Role.viewer, Role.admin) == Role.admin
    assert effective_role(Role.admin, Role.viewer) == Role.viewer
    assert effective_role(Role.builder, None) == Role.builder


def test_coerce_role_unknown_is_viewer():
    assert coerce_role("nope") == Role.viewer
    assert coerce_role(None) == Role.viewer
    assert coerce_role(Role.admin) == Role.admin


def test_role_from_groups_precedence():
    settings = _settings()
    assert role_from_groups(settings, ["ovbuilder-admins", "ovbuilder-builders"]) == Role.admin
    assert role_from_groups(settings, ["OVBUILDER-BUILDERS"]) == Role.builder
    assert role_from_groups(settings, ["ovbuilder-viewers"]) == Role.viewer
    assert role_from_groups(settings, ["unrelated"]) == Role.viewer


@pytest.mark.asyncio
async def test_resolve_override_beats_ldap_lookup(user_db, monkeypatch):
    settings = _settings(role_cache_ttl_seconds=0)
    await upsert_user(
        username="alice",
        ldap_role=Role.viewer,
        ldap_checked_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    await set_role_override("alice", Role.admin)

    def _ldap(_settings, username: str):
        return {
            "username": username,
            "groups": ["ovbuilder-viewers"],
            "email": "alice@example.com",
            "display_name": "Alice",
        }

    monkeypatch.setattr("app.auth.ldap_lookup_user", _ldap)
    record = await resolve_user_role("alice", settings, token_role=Role.viewer)
    assert record.ldap_role == Role.viewer
    assert record.role_override == Role.admin
    assert record.role == Role.admin


@pytest.mark.asyncio
async def test_stale_ldap_role_refreshed_without_waiting_for_jwt(user_db, monkeypatch):
    settings = _settings(role_cache_ttl_seconds=60)
    # Token still says admin (as an 8h JWT would after a group revocation).
    await upsert_user(
        username="bob",
        ldap_role=Role.admin,
        ldap_checked_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )

    def _ldap(_settings, username: str):
        return {"username": username, "groups": ["ovbuilder-viewers"]}

    monkeypatch.setattr("app.auth.ldap_lookup_user", _ldap)
    record = await resolve_user_role("bob", settings, token_role=Role.admin)
    assert record.ldap_role == Role.viewer
    assert record.role_override is None
    assert record.role == Role.viewer

    token = create_token("bob", Role.admin, get_settings())
    payload = decode_token(token, get_settings())
    assert payload is not None
    assert payload["role"] == "admin"


@pytest.mark.asyncio
async def test_fresh_cache_skips_ldap(user_db, monkeypatch):
    settings = _settings(role_cache_ttl_seconds=60)
    await upsert_user(
        username="cara",
        ldap_role=Role.builder,
        ldap_checked_at=datetime.now(timezone.utc),
    )

    def _boom(_settings, username: str):
        raise AssertionError("LDAP should not be called while the cache is fresh")

    monkeypatch.setattr("app.auth.ldap_lookup_user", _boom)
    record = await resolve_user_role("cara", settings, token_role=Role.admin)
    assert record.role == Role.builder


@pytest.mark.asyncio
async def test_ldap_outage_keeps_cached_role(user_db, monkeypatch):
    settings = _settings(role_cache_ttl_seconds=0)
    await upsert_user(
        username="dana",
        ldap_role=Role.builder,
        ldap_checked_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    def _down(_settings, username: str):
        raise LdapUnavailable("directory offline")

    monkeypatch.setattr("app.auth.ldap_lookup_user", _down)
    record = await resolve_user_role("dana", settings, token_role=Role.viewer)
    assert record.ldap_role == Role.builder
    assert record.role == Role.builder


@pytest.mark.asyncio
async def test_missing_ldap_user_drops_to_default_without_override(user_db, monkeypatch):
    settings = _settings(role_cache_ttl_seconds=0, ldap_default_role="viewer")
    await upsert_user(
        username="erin",
        ldap_role=Role.admin,
        ldap_checked_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    monkeypatch.setattr("app.auth.ldap_lookup_user", lambda *_a, **_k: None)
    record = await resolve_user_role("erin", settings, token_role=Role.admin)
    assert record.ldap_role == Role.viewer
    assert record.role == Role.viewer


@pytest.mark.asyncio
async def test_me_uses_override_not_jwt_role(client, monkeypatch):
    await set_role_override("alice", Role.admin)

    def _ldap(_settings, username: str):
        return {"username": username, "groups": ["ovbuilder-viewers"]}

    monkeypatch.setattr("app.auth.ldap_lookup_user", _ldap)
    res = await client.get("/api/auth/me", headers=auth_header("alice", Role.viewer))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["username"] == "alice"
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_me_reflects_ldap_revocation_on_stale_admin_token(client, monkeypatch):
    await upsert_user(
        username="bob",
        ldap_role=Role.admin,
        ldap_checked_at=datetime.now(timezone.utc) - timedelta(hours=8),
    )

    def _ldap(_settings, username: str):
        return {"username": username, "groups": []}

    monkeypatch.setattr("app.auth.ldap_lookup_user", _ldap)
    res = await client.get("/api/auth/me", headers=auth_header("bob", Role.admin))
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_login_honors_existing_override(client, monkeypatch):
    await set_role_override("root", Role.admin)

    def _auth(_settings, username: str, password: str):
        if username == "root" and password == "correct-horse":
            return {"username": "root", "groups": ["ovbuilder-viewers"]}
        return None

    monkeypatch.setattr("app.api.auth.ldap_authenticate", _auth)
    res = await client.post(
        "/api/auth/login",
        data={"username": "root", "password": "correct-horse"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["role"] == "admin"
    payload = decode_token(body["access_token"], get_settings())
    assert payload is not None
    assert payload["role"] == "admin"


@pytest.mark.asyncio
async def test_admin_set_and_clear_override(client, monkeypatch):
    await upsert_user(
        username="admin",
        ldap_role=Role.admin,
        ldap_checked_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        "app.auth.ldap_lookup_user",
        lambda *_a, **_k: {"username": "admin", "groups": ["ovbuilder-admins"]},
    )
    headers = auth_header("admin", Role.admin)

    created = await client.put(
        "/api/auth/users/newbie",
        headers=headers,
        json={"role_override": "builder"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["username"] == "newbie"
    assert created.json()["role_override"] == "builder"
    assert created.json()["role"] == "builder"

    listed = await client.get("/api/auth/users", headers=headers)
    assert listed.status_code == 200
    names = {u["username"] for u in listed.json()}
    assert "newbie" in names
    assert "admin" in names

    cleared = await client.put(
        "/api/auth/users/newbie",
        headers=headers,
        json={"role_override": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["role_override"] is None
    stored = await get_user("newbie")
    assert stored is not None
    assert stored.role_override is None


@pytest.mark.asyncio
async def test_viewer_cannot_set_override(client, monkeypatch):
    await upsert_user(
        username="viewer",
        ldap_role=Role.viewer,
        ldap_checked_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        "app.auth.ldap_lookup_user",
        lambda *_a, **_k: {"username": "viewer", "groups": ["ovbuilder-viewers"]},
    )
    res = await client.put(
        "/api/auth/users/alice",
        headers=auth_header("viewer", Role.viewer),
        json={"role_override": "admin"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_follows_resolved_role_not_jwt(client, monkeypatch):
    await set_role_override("promoted", Role.admin)
    monkeypatch.setattr(
        "app.auth.ldap_lookup_user",
        lambda *_a, **_k: {"username": "promoted", "groups": ["ovbuilder-viewers"]},
    )
    res = await client.get("/api/auth/users", headers=auth_header("promoted", Role.viewer))
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_get_current_user_direct_override(user_db, monkeypatch):
    await set_role_override("alice", Role.builder)
    monkeypatch.setattr(
        "app.auth.ldap_lookup_user",
        lambda *_a, **_k: {"username": "alice", "groups": ["ovbuilder-admins"]},
    )
    token = create_token("alice", Role.admin, get_settings())
    user = await get_current_user(token)
    assert user.role == Role.builder
