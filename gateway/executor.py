"""Generic executor — resolves user connection live from Nango, then proxies."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from . import nango
from .registry import ToolDef

log = logging.getLogger(__name__)


def _fill_path_params(path: str, params: dict[str, Any], args: dict[str, Any]) -> str:
    """Replace {param} placeholders in path with actual values from args."""
    def _replace(m: re.Match) -> str:
        key = m.group(1)
        return str(args.pop(key, f"{{{key}}}"))

    return re.sub(r'\{(\w+)\}', _replace, path)


def _extract_body(tool_def: ToolDef, args: dict[str, Any]) -> dict[str, Any] | None:
    """If the tool has a _body param schema, collect matching args into a body dict."""
    if '_body' not in tool_def.params:
        return None

    body_schema = tool_def.params['_body']
    body: dict[str, Any] = {}
    for field_name, field_def in body_schema.items():
        if field_name in args:
            body[field_name] = args[field_name]
    return body if body else None


def _apply_api_key_auth(
    headers: dict[str, Any],
    query_params: dict[str, Any],
    body: dict[str, Any] | None,
    api_key: str,
    security_scheme: dict[str, Any],
) -> None:
    """Apply API key auth based on the security scheme location."""
    scheme_type = security_scheme.get('type', '')
    if scheme_type != 'apiKey':
        return

    location = security_scheme.get('in', 'header')
    field_name = security_scheme.get('name', '')

    if location == 'header':
        headers[field_name] = api_key
    elif location == 'query':
        query_params[field_name] = api_key
    elif location == 'body':
        # API key goes into a field in the request body
        if body is not None:
            body[field_name] = api_key
        else:
            # Create body with just the key field
            # This case shouldn't normally happen (body should already exist)
            pass


def _apply_http_auth(
    headers: dict[str, Any],
    api_key: str,
    security_scheme: dict[str, Any],
) -> None:
    """Apply HTTP auth (Bearer/Basic) based on the security scheme."""
    scheme = security_scheme.get('scheme', 'bearer')
    if scheme.lower() == 'bearer':
        headers['Authorization'] = f'Bearer {api_key}'
    elif scheme.lower() == 'basic':
        import base64
        encoded = base64.b64encode(api_key.encode()).decode()
        headers['Authorization'] = f'Basic {encoded}'


async def execute_tool(
    nango_host: str,
    nango_secret: str,
    user_id: str,
    tool_def: ToolDef,
    arguments: dict[str, Any],
    user_email: str = '',
) -> dict[str, Any]:
    """
    Execute a tool call:
    - OAuth providers (security_scheme.type == 'oauth2'): resolve connection from Nango, proxy
    - API-key providers (security_scheme.type == 'apiKey'): read key from DB, apply dynamically
    - HTTP providers (security_scheme.type == 'http'): read key from DB, apply as Bearer/Basic
    - No security_scheme: try Nango (legacy path)
    """

    security = tool_def.security_scheme

    # OAuth providers: go through Nango
    if security and security.get('type') == 'oauth2':
        return await _execute_oauth_tool(nango_host, nango_secret, user_id, tool_def, arguments, user_email)

    # API-key or HTTP auth: read key from DB, apply dynamically
    if security and security.get('type') in ('apiKey', 'http'):
        return await _execute_api_key_tool(user_id, tool_def, arguments)

    # No security scheme defined: try Nango (legacy path for providers.json tools)
    return await _execute_oauth_tool(nango_host, nango_secret, user_id, tool_def, arguments, user_email)


async def _execute_oauth_tool(
    nango_host: str,
    nango_secret: str,
    user_id: str,
    tool_def: ToolDef,
    arguments: dict[str, Any],
    user_email: str = '',
) -> dict[str, Any]:
    """Execute a tool through Nango proxy (OAuth providers)."""
    connection_id = await nango.find_connection_id(
        nango_host, nango_secret, user_id, tool_def.nango_provider_key,
        user_email=user_email,
    )
    if connection_id is None:
        return {
            'success': False,
            'error': {
                'code': 'NO_CONNECTION',
                'message': f"No active connection for provider '{tool_def.provider}'. "
                f'Please connect via the dashboard first.',
            },
        }

    args = dict(arguments)
    path = _fill_path_params(tool_def.path, tool_def.params, args)
    body = _extract_body(tool_def, args)

    query_params = {
        k: v for k, v in args.items()
        if k not in ('_body',) and v is not None
    }

    return await nango.proxy_request(
        nango_host=nango_host,
        nango_secret=nango_secret,
        nango_provider_key=tool_def.nango_provider_key,
        connection_id=connection_id,
        method=tool_def.method,
        path=path,
        params=query_params if query_params else None,
        body=body,
    )


async def _execute_api_key_tool(
    user_id: str,
    tool_def: ToolDef,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a tool that uses API key or HTTP auth (dynamically from security_scheme)."""
    from .db import get_pool

    pool = await get_pool()
    row = await pool.fetchrow(
        'SELECT api_key FROM user_api_keys WHERE user_id = $1 AND provider = $2',
        user_id, tool_def.provider,
    )
    if not row:
        return {
            'success': False,
            'error': {
                'code': 'NO_API_KEY',
                'message': f"No API key configured for '{tool_def.provider}'. "
                f'Please add your API key in the dashboard.',
            },
        }

    api_key = row['api_key']
    security = tool_def.security_scheme or {}

    args = dict(arguments)
    path = _fill_path_params(tool_def.path, tool_def.params, args)
    body = _extract_body(tool_def, args)

    query_params = {
        k: v for k, v in args.items()
        if k not in ('_body',) and v is not None
    }

    headers: dict[str, str] = {}

    # Apply auth based on security scheme
    if security.get('type') == 'apiKey':
        _apply_api_key_auth(headers, query_params, body, api_key, security)
    elif security.get('type') == 'http':
        _apply_http_auth(headers, api_key, security)
    else:
        # Fallback: no security scheme, try as query param (legacy)
        query_params['key'] = api_key

    # Make direct HTTP call
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=tool_def.method,
                url=f"{tool_def.base_url}{path}",
                headers=headers if headers else None,
                params=query_params if query_params else None,
                json=body,
            )
            resp.raise_for_status()
            return {'success': True, 'data': resp.json()}
    except httpx.HTTPStatusError as e:
        return {
            'success': False,
            'error': {
                'code': f'HTTP_{e.response.status_code}',
                'message': str(e),
                'details': e.response.text[:500],
            },
        }
    except Exception as e:
        return {
            'success': False,
            'error': {
                'code': 'REQUEST_FAILED',
                'message': str(e),
            },
        }
