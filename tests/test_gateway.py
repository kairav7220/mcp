"""Tests for the gateway — auth, registry, executor, metrics."""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure gateway package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.auth import _hash_key, _extract_bearer
from gateway.registry import Registry, ToolDef
from gateway.executor import _fill_path_params, _extract_body


# ── Auth tests ───────────────────────────────────────────────────────────────

class TestAuth:
    def test_hash_key_deterministic(self):
        key = 'sk-test1234567890abcdef'
        h1 = _hash_key(key)
        h2 = _hash_key(key)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_extract_bearer_valid(self):
        assert _extract_bearer('Bearer sk-abc123') == 'sk-abc123'

    def test_extract_bearer_case_insensitive(self):
        assert _extract_bearer('bearer sk-abc123') == 'sk-abc123'
        assert _extract_bearer('BEARER sk-abc123') == 'sk-abc123'

    def test_extract_bearer_none(self):
        assert _extract_bearer(None) is None
        assert _extract_bearer('') is None

    def test_extract_bearer_no_prefix(self):
        assert _extract_bearer('sk-abc123') is None

    def test_extract_bearer_malformed(self):
        assert _extract_bearer('Bearer') is None
        assert _extract_bearer('Bearer ') is None


# ── Registry tests ───────────────────────────────────────────────────────────

class TestRegistry:
    def _write_providers_json(self, data: dict) -> str:
        fd, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        return path

    def test_load_empty(self):
        reg = Registry()
        path = self._write_providers_json({})
        count = reg.load_from_file(path)
        assert count == 0
        assert len(reg.tools) == 0
        os.unlink(path)

    def test_load_single_provider(self):
        data = {
            'gmail': {
                'nango_provider_key': 'google-gmail',
                'base_url': 'https://gmail.googleapis.com',
                'description': 'Gmail API',
                'tools': {
                    'getProfile': {
                        'name': 'getProfile',
                        'description': "Gets the user's Gmail profile.",
                        'method': 'GET',
                        'path': 'gmail/v1/users/{userId}/profile',
                        'params': {},
                    }
                },
            }
        }
        reg = Registry()
        path = self._write_providers_json(data)
        count = reg.load_from_file(path)
        assert count == 1
        assert 'gmail_getProfile' in reg.tools

        tool = reg.get('gmail_getProfile')
        assert tool is not None
        assert tool.provider == 'gmail'
        assert tool.nango_provider_key == 'google-gmail'
        assert tool.method == 'GET'
        os.unlink(path)

    def test_load_multiple_providers(self):
        data = {
            'gmail': {
                'nango_provider_key': 'google-gmail',
                'tools': {
                    'getProfile': {'description': 'Profile', 'method': 'GET', 'path': 'v1/profile'},
                    'send': {'description': 'Send email', 'method': 'POST', 'path': 'v1/send'},
                },
            },
            'slack': {
                'nango_provider_key': 'slack',
                'tools': {
                    'postMessage': {'description': 'Post', 'method': 'POST', 'path': 'chat.postMessage'},
                },
            },
        }
        reg = Registry()
        path = self._write_providers_json(data)
        count = reg.load_from_file(path)
        assert count == 3
        assert len(reg.providers()) == 2
        assert set(reg.providers()) == {'gmail', 'slack'}
        os.unlink(path)

    def test_list_by_provider(self):
        data = {
            'gmail': {
                'nango_provider_key': 'google-gmail',
                'tools': {
                    'a': {'description': 'A', 'method': 'GET', 'path': '/a'},
                    'b': {'description': 'B', 'method': 'GET', 'path': '/b'},
                },
            }
        }
        reg = Registry()
        path = self._write_providers_json(data)
        reg.load_from_file(path)
        gmail_tools = reg.list_by_provider('gmail')
        assert len(gmail_tools) == 2
        os.unlink(path)

    def test_load_missing_file(self):
        reg = Registry()
        count = reg.load_from_file('/nonexistent/path.json')
        assert count == 0


# ── Executor tests ───────────────────────────────────────────────────────────

class TestExecutor:
    def test_fill_path_params(self):
        path = 'gmail/v1/users/{userId}/messages/{id}'
        params = {'userId': {'type': 'string'}, 'id': {'type': 'string'}}
        args = {'userId': 'me', 'id': '12345', 'extra': 'keep'}
        result = _fill_path_params(path, params, args)
        assert result == 'gmail/v1/users/me/messages/12345'
        assert args == {'extra': 'keep'}  # consumed params removed

    def test_fill_path_params_missing(self):
        path = 'v1/users/{userId}'
        result = _fill_path_params(path, {}, {'other': 'val'})
        assert result == 'v1/users/{userId}'  # placeholder left if missing

    def test_extract_body_with_body(self):
        tool = ToolDef(
            name='test', provider='test', nango_provider_key='test',
            description='', method='POST', path='/test',
            params={'_body': {'name': {'type': 'string'}, 'email': {'type': 'string'}}}
        )
        args = {'name': 'Alice', 'email': 'a@b.com', 'query': 'skip'}
        body = _extract_body(tool, args)
        assert body == {'name': 'Alice', 'email': 'a@b.com'}

    def test_extract_body_no_body(self):
        tool = ToolDef(
            name='test', provider='test', nango_provider_key='test',
            description='', method='GET', path='/test',
            params={'q': {'type': 'string'}}
        )
        body = _extract_body(tool, {'q': 'hello'})
        assert body is None

    def test_extract_body_empty(self):
        tool = ToolDef(
            name='test', provider='test', nango_provider_key='test',
            description='', method='POST', path='/test',
            params={'_body': {'name': {'type': 'string'}}}
        )
        body = _extract_body(tool, {})
        assert body is None


# ── Metrics tests ────────────────────────────────────────────────────────────

class TestMetrics:
    def test_record_import(self):
        from gateway.metrics import record_tool_call, get_daily_usage
        assert callable(record_tool_call)
        assert callable(get_daily_usage)
