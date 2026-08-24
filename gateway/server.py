"""FastMCP Gateway — the main entry point.

Loads tools from providers.json, exposes them via streamable-http with
API-key auth, proxies calls through Nango, and meters every call.

Phase 2 additions: Redis client, abuse protection (rate limits, daily quota,
provider caps, circuit breaker) integrated into every tool call path.

Run:
    python gateway/server.py
    # or
    uvicorn gateway.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import asyncpg
import redis.asyncio as redis
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_http_request

from .abuse import check_abuse
from .auth import authenticate_request
from .config import get_config
from .circuit_breaker import record_call_result
from .db import apply_schema, close_pool, init_pool
from .executor import execute_tool
from .metrics import record_tool_call
from .nango import close_client
from .registry import Registry, ToolDef

log = logging.getLogger(__name__)

# ── Lifespan context ─────────────────────────────────────────────────────────

@dataclass
class GatewayContext:
    pool: asyncpg.Pool
    registry: Registry
    redis: redis.Redis
    config: Any = field(default_factory=get_config)


@asynccontextmanager
async def gateway_lifespan(server: FastMCP) -> AsyncIterator[GatewayContext]:
    config = get_config()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    log.info('Gateway starting up…')

    # Database
    pool = await init_pool(config.database_url)
    await apply_schema()

    # Redis
    redis_client = redis.from_url(config.redis_url, decode_responses=True)

    # Registry
    registry = Registry()
    count = registry.load_from_file(config.providers_json_path)
    log.info('Loaded %d tools from registry', count)

    ctx = GatewayContext(pool=pool, registry=registry, redis=redis_client, config=config)

    try:
        yield ctx
    finally:
        await redis_client.aclose()
        await close_client()
        await close_pool()
        log.info('Gateway shut down')


# ── Server (module-level export for FastMCP Cloud / uvicorn) ────────────────

mcp = FastMCP(
    name='SaaS Hub Gateway',
    lifespan=gateway_lifespan,
)


# ── Helper: get gateway context from a tool call ─────────────────────────────

def _get_ctx(context: Context) -> GatewayContext:
    return context.lifespan_context


def _get_authorization() -> str:
    """Read the API key from the HTTP Authorization header (never tool arguments)."""
    return get_http_request().headers.get('authorization', '')


# ── Health check resource ───────────────────────────────────────────────────

@mcp.resource('health://status')
async def health_check(context: Context) -> dict:
    """Gateway health: DB reachability, Redis, tool count."""
    ctx = _get_ctx(context)
    db_ok = False
    redis_ok = False

    try:
        async with ctx.pool.acquire() as conn:
            await conn.fetchval('SELECT 1')
        db_ok = True
    except Exception:
        pass

    try:
        await ctx.redis.ping()
        redis_ok = True
    except Exception:
        pass

    return {
        'status': 'healthy' if (db_ok and redis_ok) else 'degraded',
        'tools_loaded': len(ctx.registry.tools),
        'providers': ctx.registry.providers(),
        'db': db_ok,
        'redis': redis_ok,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


# ── Core tool handler (auth → abuse checks → execute → meter) ───────────────

async def _handle_tool_call(
    ctx: GatewayContext,
    tool_def: ToolDef,
    arguments: dict,
) -> dict:
    """Shared handler for both call_tool and dynamic provider tools."""
    start = time.monotonic()

    # 1. Authenticate (API key from HTTP header)
    authorization = _get_authorization()
    auth_result = await authenticate_request(authorization, ctx.pool)
    if auth_result is None:
        return {'success': False, 'error': {'code': 'UNAUTHORIZED', 'message': 'Invalid or missing API key'}}

    user_id, api_key_id = auth_result
    raw_key = authorization.split(' ', 1)[1].strip() if ' ' in authorization else authorization
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    # 2. Abuse protection (rate limits, daily quota, provider caps, circuit breaker)
    abuse_check = await check_abuse(
        redis_client=ctx.redis,
        pool=ctx.pool,
        config=ctx.config,
        key_hash=key_hash,
        user_id=user_id,
        tool_def=tool_def,
    )
    if abuse_check is not None:
        return abuse_check

    # 3. Execute via Nango
    result = await execute_tool(
        pool=ctx.pool,
        nango_host=ctx.config.nango_host,
        nango_secret=ctx.config.nango_secret,
        user_id=user_id,
        tool_def=tool_def,
        arguments=arguments,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    is_error = not result.get('success', True)
    status = 'error' if is_error else 'success'

    # 4. Meter (fire-and-forget)
    try:
        await record_tool_call(
            pool=ctx.pool,
            user_id=user_id,
            api_key_id=api_key_id,
            tool_name=tool_def.name,
            provider=tool_def.provider,
            status=status,
            duration_ms=duration_ms,
        )
    except Exception as e:
        log.warning('Metering write failed: %s', e)

    # 5. Circuit breaker — record error for provider tracking
    try:
        await record_call_result(
            r=ctx.redis,
            provider=tool_def.nango_provider_key,
            is_error=is_error,
            error_window_seconds=ctx.config.circuit_breaker_window,
        )
    except Exception as e:
        log.warning('Circuit breaker write failed: %s', e)

    return result


# ── Generic call_tool (low-level, for advanced clients) ─────────────────────

@mcp.tool()
async def call_tool(
    tool_name: str,
    arguments: dict = {},
    context: Context = None,
) -> dict:
    """
    Call any registered SaaS tool by name.

    Authentication uses the API key sent in the HTTP Authorization header.

    Args:
        tool_name: Fully qualified tool name (e.g. gmail_getProfile, slack_postMessage)
        arguments: Tool-specific arguments (path params, query params, body)

    Returns:
        Tool execution result from the target SaaS provider via Nango proxy.
    """
    ctx = _get_ctx(context)

    tool_def = ctx.registry.get(tool_name)
    if tool_def is None:
        return {'success': False, 'error': {'code': 'NOT_FOUND', 'message': f"Tool '{tool_name}' not found"}}

    return await _handle_tool_call(ctx, tool_def, arguments)


# ── Dynamically register provider tools ──────────────────────────────────────

def _register_dynamic_tools(registry: Registry) -> None:
    for tool_def in registry.list_all():
        _make_and_register_tool(tool_def)


def _make_and_register_tool(tool_def: ToolDef) -> None:
    full_desc = (
        f'[{tool_def.provider.upper()}] {tool_def.method} {tool_def.path}\n\n'
        f'{tool_def.description}'
    )

    @mcp.tool(name=tool_def.name, description=full_desc)
    async def _handler(arguments: dict = {}, context: Context = None) -> dict:
        ctx = _get_ctx(context)
        return await _handle_tool_call(ctx, tool_def, arguments)

    _handler.__name__ = tool_def.name
    _handler.__qualname__ = tool_def.name


# ── Register dynamic tools at module level ──────────────────────────────────

_registry_for_startup = Registry()
try:
    _registry_for_startup.load_from_file(get_config().providers_json_path)
    _register_dynamic_tools(_registry_for_startup)
except Exception as e:
    log.warning('Pre-load of providers.json failed (will retry at lifespan): %s', e)


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    mcp.run(transport='http', port=get_config().port)
