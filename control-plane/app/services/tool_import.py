"""Import tools into the tool_registry table (Phase 6 migration).

Supports two sources:
- providers.json (legacy one-time migration)
- OpenAPI/Swagger specs (ongoing provider onboarding)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import asyncpg

log = logging.getLogger(__name__)


async def import_providers_json(pool: asyncpg.Pool, path: str | Path) -> dict:
    """Parse providers.json and upsert all tools into tool_registry.

    Returns summary: {inserted, updated, skipped}.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f'providers.json not found at {p}')

    data = json.loads(p.read_text())
    inserted = 0
    updated = 0
    skipped = 0

    async with pool.acquire() as conn:
        for provider_key, provider_data in data.items():
            nango_key = provider_data.get('nango_provider_key', provider_key)
            tools_dict = provider_data.get('tools', {})

            for tool_name, tool_data in tools_dict.items():
                qualified_name = f'{provider_key}_{tool_name}'
                input_schema = {
                    'params': tool_data.get('params', {}),
                    'nango_provider_key': nango_key,
                }
                method = tool_data.get('method', 'GET')
                path_val = tool_data.get('path', '')
                description = tool_data.get('description', '')

                row = await conn.fetchrow(
                    'SELECT id, version, method, path, description FROM tool_registry WHERE name = $1',
                    qualified_name,
                )

                if row is None:
                    await conn.execute(
                        'INSERT INTO tool_registry '
                        '(provider, name, description, method, path, input_schema) '
                        'VALUES ($1, $2, $3, $4, $5, $6)',
                        provider_key,
                        qualified_name,
                        description,
                        method,
                        path_val,
                        json.dumps(input_schema),
                    )
                    inserted += 1
                else:
                    # Update if method, path, or description changed
                    if (row['method'] != method or row['path'] != path_val or row['description'] != description):
                        await conn.execute(
                            'UPDATE tool_registry SET method = $2, path = $3, description = $4, '
                            'input_schema = $5, version = version + 1, updated_at = now() WHERE id = $1',
                            row['id'],
                            method,
                            path_val,
                            description,
                            json.dumps(input_schema),
                        )
                        updated += 1
                    else:
                        skipped += 1

    log.info('Import complete: %d inserted, %d updated, %d skipped', inserted, updated, skipped)
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped}


# ── OpenAPI spec import ─────────────────────────────────────────────────────

_METHOD_PRIORITY = {'get': 0, 'post': 1, 'put': 2, 'patch': 3, 'delete': 4}


def _resolve_ref(spec: dict, ref: str) -> dict:
    """Resolve a $ref pointer like '#/components/schemas/Foo'."""
    parts = ref.lstrip('#/').split('/')
    node = spec
    for p in parts:
        node = node.get(p, {})
    return node


def _schema_to_params(schema: dict, spec: dict) -> dict:
    """Convert an OpenAPI schema object to our param format."""
    params = {}
    for prop_name, prop_schema in schema.get('properties', {}).items():
        param: dict[str, Any] = {
            'type': prop_schema.get('type', 'string'),
            'description': prop_schema.get('description', ''),
        }
        if prop_schema.get('enum'):
            param['enum'] = prop_schema['enum']
        if prop_name in schema.get('required', []):
            param['required'] = True
        params[prop_name] = param
    return params


def _extract_operations(spec: dict) -> list[dict]:
    """Extract operations from an OpenAPI 2.x/3.x spec."""
    operations = []
    base_url = ''

    # Extract base URL
    if 'servers' in spec and spec['servers']:
        base_url = spec['servers'][0].get('url', '')
    elif 'host' in spec:
        scheme = 'https' if 'schemes' in spec and 'https' in spec['schemes'] else 'http'
        base_url = f'{scheme}://{spec["host"]}'
        if 'basePath' in spec:
            base_url += spec['basePath']

    # Extract security schemes
    security_schemes = spec.get('components', {}).get('securitySchemes', {})
    # Swagger 2.0 uses securityDefinitions instead
    if not security_schemes:
        security_schemes = spec.get('securityDefinitions', {})

    # Global security requirement
    global_security = spec.get('security', [])

    paths = spec.get('paths', {})
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        # Path-level parameters
        path_params = path_item.get('parameters', [])

        for method in ('get', 'post', 'put', 'patch', 'delete'):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue

            op_id = op.get('operationId', '')
            summary = op.get('summary', '')
            description = op.get('description', '')
            op_summary = summary or description or op_id

            # Collect parameters (path + operation level)
            all_params = path_params + op.get('parameters', [])
            input_params = {}
            body_params = {}

            for p in all_params:
                if isinstance(p, dict) and '$ref' in p:
                    p = _resolve_ref(spec, p['$ref'])
                if not isinstance(p, dict):
                    continue

                name = p.get('name', '')
                if not name:
                    continue

                if p.get('in') == 'body':
                    body_schema = p.get('schema', {})
                    if '$ref' in body_schema:
                        body_schema = _resolve_ref(spec, body_schema['$ref'])
                    body_params = _schema_to_params(body_schema, spec)
                else:
                    param: dict[str, Any] = {
                        'type': p.get('type', 'string'),
                        'description': p.get('description', ''),
                        'required': p.get('required', False),
                    }
                    if p.get('enum'):
                        param['enum'] = p['enum']
                    input_params[name] = param

            # Request body (OpenAPI 3.x style)
            request_body = op.get('requestBody', {})
            if '$ref' in request_body:
                request_body = _resolve_ref(spec, request_body['$ref'])
            if 'content' in request_body:
                for media_type, media_obj in request_body['content'].items():
                    if 'schema' in media_obj:
                        body_schema = media_obj['schema']
                        if '$ref' in body_schema:
                            body_schema = _resolve_ref(spec, body_schema['$ref'])
                        body_params = _schema_to_params(body_schema, spec)
                    break

            # Combine params
            params = {}
            if input_params:
                params.update(input_params)
            if body_params:
                params['_body'] = body_params

            # Resolve security scheme for this operation
            op_security = op.get('security', global_security)
            security_scheme = _resolve_security_scheme(security_schemes, op_security)

            operations.append({
                'method': method.upper(),
                'path': path,
                'operation_id': op_id,
                'description': op_summary,
                'params': params,
                'base_url': base_url,
                'security_scheme': security_scheme,
            })

    return operations


def _resolve_security_scheme(schemes: dict, security_reqs: list) -> dict | None:
    """Resolve the effective security scheme from a security requirement list.

    OpenAPI security is an array of objects. Each key references a scheme in
    components.securitySchemes. We pick the first one that resolves.

    Returns a normalized dict like:
        {"type": "apiKey", "in": "header", "name": "Authorization"}
        {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        {"type": "apiKey", "in": "query", "name": "key"}
    """
    for req in security_reqs:
        if not isinstance(req, dict):
            continue
        for scheme_name in req:
            scheme_def = schemes.get(scheme_name)
            if not scheme_def:
                continue

            scheme_type = scheme_def.get('type', '')

            if scheme_type == 'apiKey':
                return {
                    'type': 'apiKey',
                    'in': scheme_def.get('in', 'header'),
                    'name': scheme_def.get('name', ''),
                }
            elif scheme_type == 'http':
                return {
                    'type': 'http',
                    'scheme': scheme_def.get('scheme', 'bearer'),
                    'bearerFormat': scheme_def.get('bearerFormat', ''),
                }
            elif scheme_type == 'oauth2' or scheme_type == 'openIdConnect':
                # OAuth providers go through Nango, not direct
                return {
                    'type': 'oauth2',
                }

    return None


async def import_openapi_spec(
    pool: asyncpg.Pool,
    provider: str,
    spec: dict,
    nango_provider_key: str | None = None,
) -> dict:
    """Parse an OpenAPI spec and upsert operations into tool_registry.

    Args:
        pool: asyncpg connection pool
        provider: provider key (e.g. 'github', 'notion')
        spec: parsed OpenAPI/Swagger JSON
        nango_provider_key: Nango provider key (defaults to provider)

    Returns summary: {inserted, updated, skipped, total}.
    """
    nango_key = nango_provider_key or provider
    operations = _extract_operations(spec)
    inserted = 0
    updated = 0
    skipped = 0

    async with pool.acquire() as conn:
        for op in operations:
            # Build qualified name: provider_operationId or provider_method_path
            op_id = op.get('operation_id', '')
            if op_id:
                # Sanitize operationId to be a valid tool name
                safe_id = re.sub(r'[^a-zA-Z0-9_]', '_', op_id).strip('_')
                qualified_name = f'{provider}_{safe_id}'
            else:
                # Fallback: provider_method_path_hash
                path_slug = re.sub(r'[^a-zA-Z0-9]', '_', op['path']).strip('_')
                qualified_name = f'{provider}_{op["method"].lower()}_{path_slug}'[:128]

            input_schema = {
                'params': op['params'],
                'nango_provider_key': nango_key,
                'base_url': op['base_url'],
            }

            security_scheme = op.get('security_scheme')

            row = await conn.fetchrow(
                'SELECT id, version, method, path, description FROM tool_registry WHERE name = $1',
                qualified_name,
            )

            if row is None:
                await conn.execute(
                    'INSERT INTO tool_registry '
                    '(provider, name, description, method, path, input_schema, security_scheme) '
                    'VALUES ($1, $2, $3, $4, $5, $6, $7)',
                    provider,
                    qualified_name,
                    op['description'],
                    op['method'],
                    op['path'],
                    json.dumps(input_schema),
                    json.dumps(security_scheme) if security_scheme else None,
                )
                inserted += 1
            else:
                if row['method'] != op['method'] or row['path'] != op['path'] or row['description'] != op['description']:
                    await conn.execute(
                        'UPDATE tool_registry SET method = $2, path = $3, description = $4, '
                        'input_schema = $5, security_scheme = $6, version = version + 1, updated_at = now() WHERE id = $1',
                        row['id'],
                        op['method'],
                        op['path'],
                        op['description'],
                        json.dumps(input_schema),
                        json.dumps(security_scheme) if security_scheme else None,
                    )
                    updated += 1
                else:
                    skipped += 1

    total = inserted + updated + skipped
    log.info('OpenAPI import (%s): %d inserted, %d updated, %d skipped (of %d operations)',
             provider, inserted, updated, skipped, total)
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped, 'total': total}
