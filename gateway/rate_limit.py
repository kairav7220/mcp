"""Rate limiting — Redis-based token bucket per API key.

Returns (allowed: bool, retry_after: float | None, limit: int, remaining: int).
Uses Redis INCR + EXPIRE for sliding-window approximation (simpler than Lua token bucket,
good enough for our scale).
"""

from __future__ import annotations

import time

import redis.asyncio as redis

# ── Key patterns ──────────────────────────────────────────────────────────────
# Rate limit: per-minute sliding window per API key
#   rl:{key_prefix}:{YYYYMMDDHHmm} → count
#
# Per-provider cap: per user per hour per provider
#   rl:prov:{user_id}:{provider}:{YYYYMMDDHH} → count


def _minute_bucket() -> str:
    return time.strftime('%Y%m%d%H%M', time.gmtime())


def _hour_bucket() -> str:
    return time.strftime('%Y%m%d%H', time.gmtime())


async def check_rate_limit(
    r: redis.Redis,
    key_hash: str,
    max_per_minute: int,
) -> tuple[bool, float | None, int, int]:
    """
    Check per-key rate limit (sliding window per minute).

    Returns (allowed, retry_after_seconds, limit, remaining).
    """
    bucket = _minute_bucket()
    key = f'rl:{key_hash[:16]}:{bucket}'

    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, 70)  # slightly more than 60s for safety
    results = await pipe.execute()

    count = results[0]
    remaining = max(0, max_per_minute - count)

    if count > max_per_minute:
        retry_after = 60 - (int(time.time()) % 60)
        return False, max(retry_after, 1), max_per_minute, 0

    return True, None, max_per_minute, remaining


async def check_provider_cap(
    r: redis.Redis,
    user_id: str,
    provider: str,
    max_per_hour: int,
) -> tuple[bool, float | None, int, int]:
    """
    Check per-user per-provider hourly cap.

    Returns (allowed, retry_after_seconds, limit, remaining).
    """
    bucket = _hour_bucket()
    key = f'rl:prov:{user_id[:8]}:{provider}:{bucket}'

    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, 3700)  # ~62 min
    results = await pipe.execute()

    count = results[0]
    remaining = max(0, max_per_hour - count)

    if count > max_per_hour:
        retry_after = 3600 - (int(time.time()) % 3600)
        return False, max(retry_after, 1), max_per_hour, 0

    return True, None, max_per_hour, remaining


async def get_key_usage(
    r: redis.Redis,
    key_hash: str,
) -> int:
    """Return current minute's call count for a key."""
    bucket = _minute_bucket()
    key = f'rl:{key_hash[:16]}:{bucket}'
    val = await r.get(key)
    return int(val) if val else 0
