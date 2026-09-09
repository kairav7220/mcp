"""Nango self-hosted HTTP API client.

Calls the Nango proxy endpoint to execute tool calls on behalf of authenticated users.
We never hold OAuth tokens — Nango does that.

Also queries Nango's API for live connection state (lazy validation).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


async def get_client(nango_host: str, nango_secret: str) -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=nango_host,
            headers={
                'Authorization': f'Bearer {nango_secret}',
                'Content-Type': 'application/json',
            },
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


async def find_connection_id(
    nango_host: str,
    nango_secret: str,
    user_id: str,
    nango_provider_key: str,
    user_email: str = '',
) -> str | None:
    """Query Nango live for the user's connection for this provider.

    This Nango self-hosted version does NOT support query params on /connections
    (returns 400 for ?userId= etc). So we fetch all and filter in Python.
    Returns the connection_id if found, None otherwise.
    """
    client = await get_client(nango_host, nango_secret)
    try:
        resp = await client.get('/connections')
        resp.raise_for_status()
        connections = resp.json().get('connections', [])

        for conn in connections:
            if conn.get('provider_config_key') != nango_provider_key:
                continue

            end_user = conn.get('end_user') or {}
            tags = conn.get('tags') or {}
            conn_ids = {
                str(end_user.get('id') or ''),
                str(tags.get('end_user_id') or ''),
                str(end_user.get('email') or ''),
                str(tags.get('end_user_email') or ''),
            }
            if user_id in conn_ids or (user_email and user_email in conn_ids):
                conn_id = conn.get('connection_id', '')
                if conn_id:
                    return conn_id

        return None
    except Exception as e:
        log.error('Nango connection lookup failed: user=%s provider=%s error=%s',
                  user_id, nango_provider_key, e)
        return None


async def proxy_request(
    nango_host: str,
    nango_secret: str,
    nango_provider_key: str,
    connection_id: str,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Forward a request through the Nango proxy.

    POST {nango_host}/proxy/{path}
    Headers: Authorization: Bearer {nango_secret},
             Provider-Config-Key: {nango_provider_key}, Connection-Id: {connection_id}
    """
    client = await get_client(nango_host, nango_secret)

    # Per Nango docs: provider key goes in a header, NOT in the URL path.
    url = f'/proxy/{path}'

    try:
        response = await client.request(
            method=method.upper(),
            url=url,
            params=params or {},
            json=body,
            headers={
                'Connection-Id': connection_id,
                'Provider-Config-Key': nango_provider_key,
            },
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        log.error(
            'Nango proxy error: provider=%s status=%d body=%s',
            nango_provider_key,
            e.response.status_code,
            e.response.text[:200],
        )
        return {
            'success': False,
            'error': {
                'code': f'HTTP_{e.response.status_code}',
                'message': e.response.text[:500],
            },
        }
    except httpx.TimeoutException:
        return {
            'success': False,
            'error': {'code': 'TIMEOUT', 'message': 'Nango proxy request timed out'},
        }
    except httpx.NetworkError as e:
        return {
            'success': False,
            'error': {'code': 'NETWORK_ERROR', 'message': str(e)},
        }
