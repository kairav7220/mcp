"""Tests for OpenAPI spec import and per-user tool permissions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'control-plane'))

from app.services.tool_import import (
    _extract_operations,
    _resolve_ref,
    _schema_to_params,
)

# ── OpenAPI parsing tests ───────────────────────────────────────────────────

class TestExtractOperations:
    def test_simple_get(self):
        spec = {
            'openapi': '3.0.0',
            'servers': [{'url': 'https://api.example.com'}],
            'paths': {
                '/users': {
                    'get': {
                        'operationId': 'listUsers',
                        'summary': 'List all users',
                        'parameters': [
                            {'name': 'limit', 'in': 'query', 'type': 'integer', 'description': 'Max results'}
                        ],
                    }
                }
            },
        }
        ops = _extract_operations(spec)
        assert len(ops) == 1
        assert ops[0]['method'] == 'GET'
        assert ops[0]['path'] == '/users'
        assert ops[0]['operation_id'] == 'listUsers'
        assert ops[0]['description'] == 'List all users'
        assert 'limit' in ops[0]['params']
        assert ops[0]['base_url'] == 'https://api.example.com'

    def test_post_with_request_body(self):
        spec = {
            'openapi': '3.0.0',
            'paths': {
                '/messages': {
                    'post': {
                        'operationId': 'sendMessage',
                        'summary': 'Send a message',
                        'requestBody': {
                            'content': {
                                'application/json': {
                                    'schema': {
                                        'type': 'object',
                                        'properties': {
                                            'to': {'type': 'string', 'description': 'Recipient'},
                                            'body': {'type': 'string', 'description': 'Message body'},
                                        },
                                        'required': ['to', 'body'],
                                    }
                                }
                            }
                        },
                    }
                }
            },
        }
        ops = _extract_operations(spec)
        assert len(ops) == 1
        assert ops[0]['method'] == 'POST'
        assert '_body' in ops[0]['params']
        assert ops[0]['params']['_body']['to']['required'] is True

    def test_swagger_2(self):
        spec = {
            'swagger': '2.0',
            'host': 'api.example.com',
            'basePath': '/v1',
            'paths': {
                '/items': {
                    'get': {
                        'operationId': 'getItems',
                        'summary': 'Get items',
                    }
                }
            },
        }
        ops = _extract_operations(spec)
        assert len(ops) == 1
        assert ops[0]['base_url'] == 'http://api.example.com/v1'

    def test_multiple_methods(self):
        spec = {
            'openapi': '3.0.0',
            'paths': {
                '/items/{id}': {
                    'get': {'operationId': 'getItem', 'summary': 'Get'},
                    'put': {'operationId': 'updateItem', 'summary': 'Update'},
                    'delete': {'operationId': 'deleteItem', 'summary': 'Delete'},
                }
            },
        }
        ops = _extract_operations(spec)
        assert len(ops) == 3
        methods = {op['method'] for op in ops}
        assert methods == {'GET', 'PUT', 'DELETE'}

    def test_ref_resolved(self):
        spec = {
            'openapi': '3.0.0',
            'components': {
                'schemas': {
                    'User': {
                        'type': 'object',
                        'properties': {
                            'name': {'type': 'string'},
                        },
                    }
                }
            },
            'paths': {
                '/users': {
                    'post': {
                        'operationId': 'createUser',
                        'requestBody': {
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/User'}
                                }
                            }
                        },
                    }
                }
            },
        }
        ops = _extract_operations(spec)
        assert len(ops) == 1
        assert '_body' in ops[0]['params']
        assert 'name' in ops[0]['params']['_body']

    def test_empty_paths(self):
        spec = {'openapi': '3.0.0', 'paths': {}}
        ops = _extract_operations(spec)
        assert ops == []


class TestSchemaToParams:
    def test_basic(self):
        schema = {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'User name'},
                'age': {'type': 'integer'},
            },
            'required': ['name'],
        }
        params = _schema_to_params(schema, {})
        assert params['name']['type'] == 'string'
        assert params['name']['required'] is True
        assert params['age'].get('required', False) is False

    def test_enum(self):
        schema = {
            'type': 'object',
            'properties': {
                'status': {'type': 'string', 'enum': ['active', 'inactive']},
            },
        }
        params = _schema_to_params(schema, {})
        assert params['status']['enum'] == ['active', 'inactive']


class TestResolveRef:
    def test_simple_ref(self):
        spec = {'components': {'schemas': {'Foo': {'type': 'string'}}}}
        result = _resolve_ref(spec, '#/components/schemas/Foo')
        assert result == {'type': 'string'}

    def test_nested_ref(self):
        spec = {'a': {'b': {'c': 42}}}
        result = _resolve_ref(spec, 'a/b/c')
        assert result == 42
