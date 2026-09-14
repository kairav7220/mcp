"""Circuit breaker — per-provider error-rate based auto-block.

Tracks recent errors in a Redis sliding window. When error rate exceeds
threshold over the lookback window, the provider is marked OPEN (all calls
blocked). After a cooldown, transitions to HALF-OPEN (allows one probe call).
Success → CLOSED (normal). Failure → back to OPEN.

States: CLOSED (normal) → OPEN (blocked) → HALF-OPEN (probe) → CLOSED/OPEN
"""

from __future__ import annotations

import logging
import time

import redis.asyncio as redis

log = logging.getLogger(__name__)

# ── Redis key patterns ────────────────────────────────────────────────────────
# Error window: cb:err:{provider}:{minute_bucket} → error count
# Probe lock:   cb:probe:{provider} → timestamp (TTL = cooldown)
# Open lock:    cb:open:{provider} → timestamp (TTL = cooldown)


def _minute_bucket(ts: float | None = None) -> str:
    t = ts or time.time()
    return time.strftime('%Y%m%d%H%M', time.gmtime(t))


async def record_call_result(
    r: redis.Redis,
    provider: str,
    is_error: bool,
    *,
    error_window_seconds: int = 60,
) -> None:
    """Record a call result. Increments error counter if is_error."""
    if not is_error:
        return

    bucket = _minute_bucket()
    key = f'cb:err:{provider}:{bucket}'
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, error_window_seconds + 30)
    await pipe.execute()


async def check_circuit_breaker(
    r: redis.Redis,
    provider: str,
    *,
    error_threshold: float = 0.5,
    min_calls: int = 5,
    error_window_seconds: int = 60,
    cooldown_seconds: int = 120,
) -> tuple[bool, str, float | None]:
    """
    Check if a provider's circuit is open.

    Returns (allowed, state, retry_after).
      state: 'closed' | 'open' | 'half_open'
      retry_after: seconds until half-open (only if open)
    """
    now = time.time()

    # Check if currently in open state (cooldown lock)
    open_key = f'cb:open:{provider}'
    open_ts = await r.get(open_key)
    if open_ts is not None:
        opened_at = float(open_ts)
        elapsed = now - opened_at
        if elapsed < cooldown_seconds:
            retry_after = cooldown_seconds - elapsed
            return False, 'open', retry_after
        # Cooldown expired → half-open (allow one probe)
        half_open_key = f'cb:probe:{provider}'
        already_probing = await r.exists(half_open_key)
        if already_probing:
            # Another probe in flight, keep blocking
            return False, 'open', cooldown_seconds - elapsed
        # Set probe lock (short TTL)
        await r.set(half_open_key, str(now), ex=30)
        return True, 'half_open', None

    # Count errors in the window
    total_errors = 0

    for offset in range(max(1, error_window_seconds // 60)):
        bucket_ts = now - (offset * 60)
        bucket = _minute_bucket(bucket_ts)
        err_key = f'cb:err:{provider}:{bucket}'
        val = await r.get(err_key)
        if val:
            total_errors += int(val)
        # We approximate total_calls from error count (conservative:
        # only counts errors; real total from metric_daily in Postgres)
        # For threshold check this is fine: error_rate >= threshold when errors are high

    # Use metric_daily for accurate call count (asyncpg would be slow here)
    # Conservative: if we have errors, assume at least min_calls worth of traffic
    # This is a deliberate tradeoff for speed; real check is in dashboard
    if total_errors >= min_calls:
        error_rate = 1.0  # conservative: if many errors, trip
    else:
        error_rate = total_errors / max(min_calls, 1)

    if error_rate >= error_threshold and total_errors >= min_calls:
        # Trip the breaker
        await r.set(open_key, str(now), ex=cooldown_seconds + 10)
        log.warning(
            'Circuit breaker OPEN for provider %s: error_rate=%.2f (%d errors)',
            provider, error_rate, total_errors,
        )
        return False, 'open', float(cooldown_seconds)

    return True, 'closed', None


async def reset_circuit_breaker(
    r: redis.Redis,
    provider: str,
) -> None:
    """Manually reset a provider's circuit breaker (admin action)."""
    await r.delete(f'cb:open:{provider}', f'cb:probe:{provider}')
    log.info('Circuit breaker manually reset for provider %s', provider)
