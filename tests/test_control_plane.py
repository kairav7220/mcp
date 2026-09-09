"""Tests for control plane — webhook signature, sync logic, catalog, Nango client."""

import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# control-plane has a hyphen → imported as package "app" via its own dir on sys.path
sys.path.insert(0, str(ROOT / 'control-plane'))

from app.routers.connections import verify_signature
from app.services import catalog as catalog_svc


# ── Webhook signature ────────────────────────────────────────────────────────

class TestWebhookSignature:
    BODY = b'{"event": "connectioncreation"}'

    def test_valid_signature(self):
        secret = 'whsec_abc123'
        sig = hmac.new(secret.encode(), self.BODY, hashlib.sha256).hexdigest()
        assert verify_signature(secret, self.BODY, sig) is True

    def test_invalid_signature(self):
        assert verify_signature('whsec_abc123', self.BODY, 'deadbeef') is False

    def test_missing_signature_header(self):
        assert verify_signature('whsec_abc123', self.BODY, None) is False

    def test_dev_mode_no_secret_allows_all(self):
        assert verify_signature('', self.BODY, None) is True
        assert verify_signature('', self.BODY, 'anything') is True

    def test_case_insensitive_compare(self):
        secret = 'whsec_abc123'
        sig = hmac.new(secret.encode(), self.BODY, hashlib.sha256).hexdigest()
        assert verify_signature(secret, self.BODY, sig.upper()) is True


# ── Webhook payload handling (type/operation per Nango docs) ─────────────────


class TestWebhookPayload:
    def _post(self, payload: dict, monkeypatch):
        from fastapi.testclient import TestClient

        os.environ.setdefault('DATABASE_URL', 'postgresql://x')
        os.environ.setdefault('NANGO_SECRET', 'test')
        from app.routers import connections as conn_mod
        from app.main import app
        from app.config import get_config

        async def fake_upsert(user_id, provider, connection_id, status):
            TestWebhookPayload.last_upsert = (user_id, provider, connection_id, status)

        async def fake_delete(connection_id):
            return True

        monkeypatch.setattr(conn_mod.db, 'upsert_connection', fake_upsert)
        monkeypatch.setattr(conn_mod.db, 'delete_connection', fake_delete)

        self.last_upsert = None
        body = json.dumps(payload).encode()
        headers = {'Content-Type': 'application/json'}
        signing_key = get_config().nango_webhook_signing_key
        if signing_key:
            headers['X-Nango-Hmac-Sha256'] = hmac.new(
                signing_key.encode(), body, hashlib.sha256
            ).hexdigest()

        client = TestClient(app)
        return client.post('/api/v1/connections/webhook', content=body, headers=headers)

    def test_auth_creation_syncs_active(self, monkeypatch):
        r = self._post({
            'type': 'auth', 'operation': 'creation', 'success': True,
            'connectionId': 'conn-1', 'providerConfigKey': 'google-gmail',
            'tags': {'end_user_id': 'u-1'},
        }, monkeypatch)
        assert r.status_code == 200
        assert r.json()['action'] == 'active'
        assert TestWebhookPayload.last_upsert == ('u-1', 'google-gmail', 'conn-1', 'active')

    def test_refresh_failure_marks_error(self, monkeypatch):
        r = self._post({
            'type': 'auth', 'operation': 'refresh', 'success': False,
            'connectionId': 'conn-2', 'providerConfigKey': 'slack',
            'tags': {'end_user_id': 'u-1'},
        }, monkeypatch)
        assert r.status_code == 200
        assert r.json()['action'] == 'error'
        assert TestWebhookPayload.last_upsert[3] == 'error'

    def test_deletion_removes_row(self, monkeypatch):
        r = self._post({
            'type': 'auth', 'operation': 'deletion',
            'connectionId': 'conn-3', 'providerConfigKey': 'slack',
            'tags': {'end_user_id': 'u-1'},
        }, monkeypatch)
        assert r.status_code == 200
        assert r.json()['action'] == 'deleted'

    def test_sync_type_ignored(self, monkeypatch):
        r = self._post({'type': 'sync', 'connectionId': 'conn-1', 'success': True}, monkeypatch)
        assert r.status_code == 200
        assert r.json()['ignored'].startswith('unhandled type')

    def test_unresolvable_user_ignored(self, monkeypatch):
        r = self._post({
            'type': 'auth', 'operation': 'creation', 'success': True,
            'connectionId': 'conn-4', 'providerConfigKey': 'slack',
            'tags': {},
        }, monkeypatch)
        assert r.status_code == 200
        assert r.json()['ignored'] == 'unresolvable user'


# ── Provider catalog ─────────────────────────────────────────────────────────

class TestCatalog:
    def _write(self, data: dict) -> str:
        fd, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        return path

    def test_load_catalog_basic(self):
        data = {
            'gmail': {
                'nango_provider_key': 'google-gmail',
                'base_url': 'https://gmail.googleapis.com',
                'description': 'Gmail API tools',
                'tools': {'a': {}, 'b': {}, 'c': {}},
            },
            'slack': {
                'nango_provider_key': 'slack',
                'description': 'Slack API',
                'tools': {'x': {}},
            },
        }
        path = self._write(data)
        cat = catalog_svc.load_catalog(path)
        os.unlink(path)

        assert len(cat) == 2
        gmail = next(c for c in cat if c['provider'] == 'gmail')
        assert gmail['tool_count'] == 3
        assert gmail['name'] == 'Gmail'
        slack = next(c for c in cat if c['provider'] == 'slack')
        assert slack['tool_count'] == 1

    def test_unknown_provider_basic_entry(self):
        data = {'weirdservice': {'tools': {}}}
        path = self._write(data)
        cat = catalog_svc.load_catalog(path)
        os.unlink(path)
        assert cat[0]['provider'] == 'weirdservice'
        assert cat[0]['tool_count'] == 0

    def test_sorted_by_tool_count_desc(self):
        data = {
            'small': {'tools': {'a': {}}},
            'big': {'tools': {'a': {}, 'b': {}, 'c': {}, 'd': {}, 'e': {}}},
        }
        path = self._write(data)
        cat = catalog_svc.load_catalog(path)
        os.unlink(path)
        assert cat[0]['provider'] == 'big'

    def test_missing_file_returns_empty(self):
        assert catalog_svc.load_catalog('/nonexistent/x.json') == []

    def test_corrupt_file_returns_empty(self):
        fd, path = tempfile.mkstemp(suffix='.json')
        os.write(fd, b'not json{')
        os.close(fd)
        assert catalog_svc.load_catalog(path) == []
        os.unlink(path)


# ── Nango admin client (mocked HTTP) ─────────────────────────────────────────

class TestNangoAdmin:
    @pytest.mark.asyncio
    async def test_create_connect_session_payload(self):
        from app.services import nango_admin

        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {'data': {'token': 'tok_123', 'expires_at': 'soon', 'connect_link': 'http://x'}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json=None, headers=None):
                captured['url'] = url
                captured['json'] = json
                captured['headers'] = headers
                return FakeResp()

        with patch.object(nango_admin.httpx, 'AsyncClient', return_value=FakeClient()):
            result = await nango_admin.create_connect_session(
                'http://nango:3003',
                'secret-key-1',
                end_user_id='u-123',
                end_user_email='a@b.com',
            )

        assert result['token'] == 'tok_123'
        assert result['connect_url'] == 'http://x'
        # POST /connect/sessions (plural) with SECRET key, identity in tags
        assert captured['url'] == 'http://nango:3003/connect/sessions'
        assert captured['headers']['Authorization'] == 'Bearer secret-key-1'
        assert captured['json']['tags']['end_user_id'] == 'u-123'
        assert captured['json']['tags']['end_user_email'] == 'a@b.com'
