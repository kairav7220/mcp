"""Nango self-hosted HTTP API client.

Calls the Nango proxy endpoint to execute tool calls on behalf of authenticated users.
We never hold OAuth tokens — Nango does that.
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

    POST {nango_host}/proxy/{nango_provider_key}/{path}
    Headers: Authorization: Bearer {nango_secret}, Connection-Id: {connection_id}
    """
    client = await get_client(nango_host, nango_secret)

    url = f'/proxy/{nango_provider_key}/{path}'

    try:
        response = await client.request(
            method=method.upper(),
            url=url,
            params=params or {},
            json=body,
            headers={'Connection-Id': connection_id},
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
