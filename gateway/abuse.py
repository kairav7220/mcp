"""Abuse protection — pre-execution checks before every tool call.

Runs all checks in sequence; returns first failure as a structured error dict.
All errors follow the format:
    {"success": false, "error": {"code": "...", "message": "...", "details": {...}}}
"""

from __future__ import annotations

import logging

import asyncpg
import redis.asyncio as redis

from .circuit_breaker import check_circuit_breaker
from .config import Config
from .metrics import get_daily_usage
from .rate_limit import check_provider_cap, check_rate_limit
from .registry import ToolDef

log = logging.getLogger(__name__)


def _deny(code: str, message: str, **details) -> dict:
    return {'success': False, 'error': {'code': code, 'message': message, **details}}


async def check_abuse(
    *,
    redis_client: redis.Redis,
    pool: asyncpg.Pool,
    config: Config,
    key_hash: str,
    user_id: str,
    tool_def: ToolDef,
) -> dict | None:
    """
    Run all pre-execution abuse checks. Returns None if allowed,
    or an error dict if blocked.
    """
    # 1. Per-key rate limit (per minute)
    allowed, retry_after, limit, remaining = await check_rate_limit(
        r=redis_client,
        key_hash=key_hash,
        max_per_minute=config.rate_limit_per_minute,
    )
    if not allowed:
        return _deny(
            'RATE_LIMITED',
            f'Rate limit exceeded ({limit} calls/min). Retry in {int(retry_after)}s.',
            retry_after=int(retry_after),
            limit=limit,
            remaining=0,
        )

    # 2. Per-user daily quota
    daily_usage = await get_daily_usage(pool, user_id)
    if daily_usage >= config.daily_quota:
        return _deny(
            'QUOTA_EXCEEDED',
            f'Daily quota exceeded ({daily_usage}/{config.daily_quota}). Resets at 00:00 UTC.',
            used=daily_usage,
            limit=config.daily_quota,
            resets_at='00:00 UTC',
        )

    # 3. Per-provider hourly cap
    allowed, retry_after, limit, remaining = await check_provider_cap(
        r=redis_client,
        user_id=user_id,
        provider=tool_def.nango_provider_key,
        max_per_hour=config.provider_cap_per_hour,
    )
    if not allowed:
        return _deny(
            'PROVIDER_CAP',
            f'Provider cap reached for {tool_def.provider} ({limit}/hr). Retry in {int(retry_after)}s.',
            provider=tool_def.provider,
            retry_after=int(retry_after),
            limit=limit,
        )

    # 4. Circuit breaker (provider error rate)
    allowed, state, retry_after = await check_circuit_breaker(
        r=redis_client,
        provider=tool_def.nango_provider_key,
        error_threshold=config.circuit_breaker_threshold,
        min_calls=config.circuit_breaker_min_calls,
        error_window_seconds=config.circuit_breaker_window,
        cooldown_seconds=config.circuit_breaker_cooldown,
    )
    if not allowed:
        return _deny(
            'CIRCUIT_OPEN',
            f'Provider {tool_def.provider} is temporarily unavailable (high error rate). '
            f'Retry in {int(retry_after)}s.',
            provider=tool_def.provider,
            state=state,
            retry_after=int(retry_after),
        )

    # All checks passed
    return None
