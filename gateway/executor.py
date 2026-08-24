"""Generic executor — resolves user connection then proxies through Nango."""

from __future__ import annotations

import logging
import re
from typing import Any

import asyncpg

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


async def execute_tool(
    pool: asyncpg.Pool,
    nango_host: str,
    nango_secret: str,
    user_id: str,
    tool_def: ToolDef,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute a tool call:
    1. Resolve the user's Nango connection for this provider
    2. Fill path params, extract body
    3. Proxy through Nango
    """
    # 1. Look up user's connection for this provider
    row = await pool.fetchrow(
        """
        SELECT nango_connection_id
        FROM user_connections
        WHERE user_id = $1 AND provider = $2 AND status = 'active'
        """,
        user_id,
        tool_def.nango_provider_key,
    )
    if row is None:
        return {
            'success': False,
            'error': {
                'code': 'NO_CONNECTION',
                'message': f"No active connection for provider '{tool_def.provider}'. "
                f'Please connect via the dashboard first.',
            },
        }

    connection_id = row['nango_connection_id']

    # 2. Prepare request
    args = dict(arguments)  # copy so we can pop from it
    path = _fill_path_params(tool_def.path, tool_def.params, args)
    body = _extract_body(tool_def, args)

    # Remaining args become query params (skip _body fields)
    query_params = {
        k: v for k, v in args.items()
        if k not in ('_body',) and v is not None
    }

    # 3. Proxy through Nango
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
