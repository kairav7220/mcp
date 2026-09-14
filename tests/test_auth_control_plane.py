"""Tests for Phase 4 — security primitives, auth endpoints, admin guards."""

import hashlib
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# control-plane has a hyphen → imported as package "app" via its own dir on sys.path
CP = str(ROOT / 'control-plane')
if CP not in sys.path:
    sys.path.insert(0, CP)

from app.security import AuthError, generate_api_key, verify_supabase_jwt

SECRET = 'test-jwt-secret-0123456789abcdef0123456789abcdef'


def make_token(sub='supabase-uid-1', email='alice@example.com', offset=3600, sign=SECRET):
    now = int(time.time())
    return jwt.encode(
        {'sub': sub, 'email': email, 'iat': now, 'exp': now + offset, 'iss': 'supabase'},
        sign, algorithm='HS256',
    )


USER_ROW = {
    'id': '11111111-1111-1111-1111-111111111111',
    'email': 'alice@example.com',
    'name': 'Alice',
    'role': 'member',
    'status': 'active',
}


# ── Security primitives ──────────────────────────────────────────────────────

class TestVerifyJwt:
    def test_valid_token(self):
        claims = verify_supabase_jwt(make_token(), SECRET)
        assert claims['sub'] == 'supabase-uid-1'
        assert claims['email'] == 'alice@example.com'

    def test_expired_rejected(self):
        with pytest.raises(AuthError):
            verify_supabase_jwt(make_token(offset=-10), SECRET)

    def test_wrong_signature_rejected(self):
        with pytest.raises(AuthError):
            verify_supabase_jwt(make_token(sign='other-secret'), SECRET)

    def test_garbage_rejected(self):
        with pytest.raises(AuthError):
            verify_supabase_jwt('not.a.jwt', SECRET)

    def test_empty_secret_fails_closed(self):
        with pytest.raises(AuthError):
            verify_supabase_jwt(make_token(), '')

    def test_empty_token_fails_closed(self):
        with pytest.raises(AuthError):
            verify_supabase_jwt('', SECRET)


class TestApiKeyGeneration:
    def test_format(self):
        raw, key_hash, prefix = generate_api_key()
        assert raw.startswith('sk-')
        assert len(key_hash) == 64
        assert key_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert prefix == raw[:11]

    def test_unique(self):
        a = generate_api_key()
        b = generate_api_key()
        assert a[0] != b[0]


# ── Endpoint tests (mini-app per router) ─────────────────────────────────────

@pytest.fixture()
def auth_client(monkeypatch):
    from app.config import Config
    from app.routers import auth as auth_router

    cfg = Config.__new__(Config)
    cfg.supabase_jwt_secret = SECRET
    cfg.supabase_url = ''
    cfg.database_url = 'x'
    cfg.nango_host = 'x'
    cfg.nango_secret = 'x'
    cfg.nango_public_key = ''
    cfg.nango_webhook_signing_key = ''
    cfg.providers_json_path = 'x'
    cfg.ui_url = 'x'
    cfg.app_url = 'x'
    cfg.webhook_url_override = ''
    cfg.log_level = 'INFO'
    monkeypatch.setattr(auth_router, 'get_config', lambda: cfg)
    monkeypatch.setattr(auth_router, 'verify_supabase_jwt', __import__('app.security', fromlist=['verify_supabase_jwt']).verify_supabase_jwt)

    pool = AsyncMock()
    # supabase_id lookup miss -> email-link hit
    pool.fetchrow.side_effect = [None, dict(USER_ROW)]
    monkeypatch.setattr(auth_router, 'get_pool', AsyncMock(return_value=pool))

    application = FastAPI()
    application.include_router(auth_router.router)
    return TestClient(application)


class TestAuthEndpoints:
    def test_verify_token_returns_profile(self, auth_client):
        resp = auth_client.post('/api/v1/auth', json={'access_token': make_token()})
        assert resp.status_code == 200
        body = resp.json()
        assert body['email'] == USER_ROW['email']
        assert body['role'] == 'member'

    def test_bad_token_401(self, auth_client):
        resp = auth_client.post('/api/v1/auth', json={'access_token': 'garbage'})
        assert resp.status_code == 401


@pytest.fixture()
def me_client(monkeypatch):
    """Mini app exposing /me + /users to exercise deps (current user + admin guard)."""
    import app.deps as deps_mod
    from app.config import Config

    cfg = Config.__new__(Config)
    cfg.supabase_jwt_secret = SECRET
    cfg.supabase_url = ''
    monkeypatch.setattr(deps_mod, 'get_config', lambda: cfg)

    def _pool_with(user_row):
        pool = AsyncMock()
        pool.fetchrow.return_value = dict(user_row) if user_row else None
        return pool

    captured = {}

    def set_user(user_row):
        captured['pool'] = _pool_with(user_row)
        monkeypatch.setattr(deps_mod, 'get_pool', AsyncMock(return_value=captured['pool']))

    monkeypatch.setattr(deps_mod, 'set_user_for_test', set_user, raising=False)
    captured['set'] = set_user

    application = FastAPI()

    from app.deps import get_current_user, require_admin
    from fastapi import Depends

    @application.get('/me')
    async def me(user: dict = Depends(get_current_user)):
        return {'email': user['email'], 'role': user['role']}

    @application.get('/admin-only')
    async def admin_only(_: dict = Depends(require_admin)):
        return {'ok': True}

    return TestClient(application), captured


class TestDeps:
    def test_me_ok(self, me_client):
        client, cap = me_client
        cap['set'](USER_ROW)
        r = client.get('/me', headers={'Authorization': f'Bearer {make_token()}'})
        assert r.status_code == 200
        assert r.json()['email'] == USER_ROW['email']

    def test_me_no_bearer_401(self, me_client):
        client, cap = me_client
        r = client.get('/me')
        assert r.status_code == 401

    def test_me_disabled_user_403(self, me_client):
        client, cap = me_client
        cap['set']({**USER_ROW, 'status': 'disabled'})
        r = client.get('/me', headers={'Authorization': f'Bearer {make_token()}'})
        assert r.status_code == 403

    def test_me_unprovisioned_403(self, me_client):
        client, cap = me_client
        cap['set'](None)  # both lookups miss
        r = client.get('/me', headers={'Authorization': f'Bearer {make_token()}'})
        assert r.status_code == 403

    def test_admin_guard_blocks_member(self, me_client):
        client, cap = me_client
        cap['set'](USER_ROW)  # role: member
        r = client.get('/admin-only', headers={'Authorization': f'Bearer {make_token()}'})
        assert r.status_code == 403

    def test_admin_guard_allows_admin(self, me_client):
        client, cap = me_client
        cap['set']({**USER_ROW, 'role': 'admin'})
        r = client.get('/admin-only', headers={'Authorization': f'Bearer {make_token()}'})
        assert r.status_code == 200


# ── Connections auth (Phase 4 fix: finding #5 closed) ────────────────────────

MEMBER_ID = '22222222-2222-2222-2222-222222222222'
ADMIN_ID = '33333333-3333-3333-3333-333333333333'


@pytest.fixture()
def conn_app(monkeypatch):
    """Mini app for /connections/* with get_current_user overridden."""
    os.environ.setdefault('DATABASE_URL', 'postgresql://x')
    os.environ.setdefault('NANGO_SECRET', 'test')
    from uuid import UUID as _UUID

    import app.routers.connections_api as conn_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    state = {'user': dict(USER_ROW, id=MEMBER_ID)}

    def current_user():
        return dict(state['user'])

    captured = {}

    async def fake_ensure_user(email, name=None):
        return 'u-internal-1'

    async def fake_create_session(host, secret, **kw):
        captured.update(kw)
        return {'token': 'tok', 'connect_url': 'http://x', 'expires_at': 'soon'}

    async def fake_nango_delete(host, secret, cid):
        return None

    async def fake_db_delete(cid):
        return True

    monkeypatch.setattr(conn_api.db, 'ensure_user', fake_ensure_user)
    monkeypatch.setattr(conn_api.nango_admin, 'create_connect_session', fake_create_session)
    monkeypatch.setattr(conn_api.nango_admin, 'delete_connection', fake_nango_delete)
    monkeypatch.setattr(conn_api.db, 'delete_connection', fake_db_delete)

    application = FastAPI()
    application.include_router(conn_api.router)
    application.dependency_overrides[conn_api.get_current_user] = current_user
    client = TestClient(application)
    return client, captured, state, _UUID


class TestConnectionsAuth:
    def test_member_forced_to_own_identity(self, conn_app):
        client, captured, _, _ = conn_app
        r = client.post('/api/v1/connections/session', json={})
        assert r.status_code == 200
        assert captured['end_user_email'] == USER_ROW['email']

    def test_member_cannot_target_other_email(self, conn_app):
        client, _, _, _ = conn_app
        r = client.post('/api/v1/connections/session',
                        json={'email': 'victim@example.com'})
        assert r.status_code == 403

    def test_admin_may_target_other_email(self, conn_app):
        client, captured, state, _ = conn_app
        state['user']['role'] = 'admin'
        r = client.post('/api/v1/connections/session',
                        json={'email': 'target@example.com', 'name': 'T'})
        assert r.status_code == 200
        assert captured['end_user_email'] == 'target@example.com'

    def test_member_list_forced_to_own(self, conn_app, monkeypatch):
        client, _, state, UUID = conn_app
        seen = {}

        async def fake_list(host, secret, *, user_id=None):
            seen['user_id'] = user_id
            return []

        import app.routers.connections_api as conn_api
        monkeypatch.setattr(conn_api.nango_admin, 'list_connections', fake_list)
        r = client.get('/api/v1/connections', params={'user_id': str(UUID('99999999-9999-9999-9999-999999999999'))})
        assert r.status_code == 200
        # member's forced filter must be their OWN id, not the requested one
        assert seen['user_id'] == MEMBER_ID

    def test_admin_can_filter_by_user_id(self, conn_app, monkeypatch):
        client, _, state, UUID = conn_app
        state['user']['role'] = 'admin'
        seen = {}

        async def fake_list(host, secret, *, user_id=None):
            seen['user_id'] = user_id
            return []

        import app.routers.connections_api as conn_api
        monkeypatch.setattr(conn_api.nango_admin, 'list_connections', fake_list)
        target = UUID('99999999-9999-9999-9999-999999999999')
        r = client.get('/api/v1/connections', params={'user_id': str(target)})
        assert r.status_code == 200
        assert seen['user_id'] == str(target)

    def test_delete_proxied_to_nango(self, conn_app, monkeypatch):
        client, _, _, _ = conn_app
        deleted = {}

        async def fake_delete(host, secret, cid, provider):
            deleted['cid'] = cid
            deleted['provider'] = provider

        import app.routers.connections_api as conn_api
        monkeypatch.setattr(conn_api.nango_admin, 'delete_connection', fake_delete)
        r = client.delete('/api/v1/connections/conn-x', params={'provider': 'slack'})
        assert r.status_code == 200
        assert r.json()['ok'] is True
        assert deleted['cid'] == 'conn-x'
        assert deleted['provider'] == 'slack'

    def test_delete_nango_failure_returns_502(self, conn_app, monkeypatch):
        client, _, _, _ = conn_app

        async def fake_delete(host, secret, cid, provider):
            raise RuntimeError('Nango down')

        import app.routers.connections_api as conn_api
        monkeypatch.setattr(conn_api.nango_admin, 'delete_connection', fake_delete)
        r = client.delete('/api/v1/connections/conn-y', params={'provider': 'slack'})
        assert r.status_code == 502


# ── Users: duplicate email → 409 (not 500) ───────────────────────────────────

class TestCreateUserConflict:
    def test_duplicate_email_409(self, monkeypatch):
        os.environ.setdefault('DATABASE_URL', 'postgresql://x')
        os.environ.setdefault('NANGO_SECRET', 'test')
        import asyncpg
        from app.routers import users as users_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        class FakePool:
            async def fetchrow(self, sql, *args):
                raise asyncpg.UniqueViolationError('duplicate key')

            async def execute(self, *a, **k):
                return 'INSERT 0 1'

        async def fake_audit(*a, **k):
            pass

        monkeypatch.setattr(users_router, 'get_pool', AsyncMock(return_value=FakePool()))
        monkeypatch.setattr(users_router, '_audit', fake_audit)

        application = FastAPI()
        application.include_router(users_router.router)
        application.dependency_overrides[users_router.require_admin] = lambda: {
            **USER_ROW, 'id': ADMIN_ID, 'role': 'admin'}
        client = TestClient(application)

        resp = client.post('/api/v1/users',
                           json={'email': 'alice@example.com', 'name': 'Alice'})
        assert resp.status_code == 409
