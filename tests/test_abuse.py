"""Tests for Phase 2 abuse protection modules.

These tests mock Redis to run without a live instance.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.abuse import _deny, check_abuse
from gateway.circuit_breaker import (
    check_circuit_breaker,
    record_call_result,
    reset_circuit_breaker,
)
from gateway.rate_limit import (
    _hour_bucket,
    _minute_bucket,
    check_provider_cap,
    check_rate_limit,
)


def _mock_redis(pipe_execute_result=None):
    """Create a mock Redis with a properly configured pipeline."""
    r = MagicMock()  # MagicMock, not AsyncMock — pipeline() is sync in redis.asyncio
    r.pipeline = MagicMock()  # explicitly sync
    pipe = MagicMock()
    # execute() IS async in redis.asyncio
    pipe.execute = AsyncMock(return_value=pipe_execute_result or [])
    pipe.incr.return_value = pipe
    pipe.expire.return_value = pipe
    r.pipeline.return_value = pipe
    # Async methods on Redis
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.exists = AsyncMock(return_value=0)
    r.delete = AsyncMock()
    r.ping = AsyncMock()
    r.aclose = AsyncMock()
    return r, pipe


# ── Rate limit tests ─────────────────────────────────────────────────────────

class TestRateLimit:
    def test_minute_bucket_format(self):
        bucket = _minute_bucket()
        assert len(bucket) == 12  # YYYYMMDDHHMM
        assert bucket.isdigit()

    def test_hour_bucket_format(self):
        bucket = _hour_bucket()
        assert len(bucket) == 10  # YYYYMMDDHH
        assert bucket.isdigit()

    @pytest.mark.asyncio
    async def test_check_rate_limit_under_limit(self):
        r, pipe = _mock_redis(pipe_execute_result=[1])

        allowed, retry_after, limit, remaining = await check_rate_limit(r, 'abc123', max_per_minute=60)

        assert allowed is True
        assert retry_after is None
        assert limit == 60
        assert remaining == 59

    @pytest.mark.asyncio
    async def test_check_rate_limit_over_limit(self):
        r, pipe = _mock_redis(pipe_execute_result=[61])

        allowed, retry_after, limit, remaining = await check_rate_limit(r, 'abc123', max_per_minute=60)

        assert allowed is False
        assert retry_after is not None
        assert retry_after > 0
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_check_provider_cap_under_limit(self):
        r, pipe = _mock_redis(pipe_execute_result=[10])

        allowed, retry_after, limit, remaining = await check_provider_cap(r, 'user1', 'gmail', max_per_hour=200)

        assert allowed is True
        assert retry_after is None
        assert remaining == 190

    @pytest.mark.asyncio
    async def test_check_provider_cap_over_limit(self):
        r, pipe = _mock_redis(pipe_execute_result=[201])

        allowed, retry_after, limit, remaining = await check_provider_cap(r, 'user1', 'gmail', max_per_hour=200)

        assert allowed is False
        assert retry_after is not None
        assert remaining == 0


# ── Circuit breaker tests ────────────────────────────────────────────────────

class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_record_error_increments(self):
        r, pipe = _mock_redis()

        await record_call_result(r, 'gmail', is_error=True, error_window_seconds=60)
        pipe.incr.assert_called_once()
        pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_success_noop(self):
        r = AsyncMock()

        await record_call_result(r, 'gmail', is_error=False)
        r.pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_circuit_closed_when_no_errors(self):
        r = AsyncMock()
        r.get.return_value = None  # not in open state
        r.exists.return_value = 0

        allowed, state, retry_after = await check_circuit_breaker(
            r, 'gmail',
            error_threshold=0.5,
            min_calls=5,
            error_window_seconds=60,
            cooldown_seconds=120,
        )

        assert allowed is True
        assert state == 'closed'

    @pytest.mark.asyncio
    async def test_circuit_open_when_in_cooldown(self):
        import time
        r = AsyncMock()
        r.get.return_value = str(time.time() - 30)  # opened 30s ago

        allowed, state, retry_after = await check_circuit_breaker(
            r, 'gmail',
            error_threshold=0.5,
            min_calls=5,
            error_window_seconds=60,
            cooldown_seconds=120,
        )

        assert allowed is False
        assert state == 'open'
        assert retry_after is not None
        assert retry_after > 0

    @pytest.mark.asyncio
    async def test_reset_circuit_breaker(self):
        r = AsyncMock()

        await reset_circuit_breaker(r, 'gmail')
        r.delete.assert_called_once_with('cb:open:gmail', 'cb:probe:gmail')


# ── Abuse check tests ────────────────────────────────────────────────────────

class TestAbuse:
    def test_deny_format(self):
        result = _deny('RATE_LIMITED', 'Too fast', retry_after=30)
        assert result['success'] is False
        assert result['error']['code'] == 'RATE_LIMITED'
        assert result['error']['retry_after'] == 30

    @pytest.mark.asyncio
    async def test_check_abuse_allows_normal_call(self):
        r, pipe = _mock_redis(pipe_execute_result=[1])
        r.get.return_value = None  # no open circuit

        pool = AsyncMock()
        row = MagicMock()
        row.__getitem__ = lambda self, key: 100  # 100 calls used
        pool.fetchrow = AsyncMock(return_value=row)

        config = MagicMock()
        config.rate_limit_per_minute = 60
        config.daily_quota = 5000
        config.provider_cap_per_hour = 200
        config.circuit_breaker_threshold = 0.5
        config.circuit_breaker_min_calls = 5
        config.circuit_breaker_window = 60
        config.circuit_breaker_cooldown = 120

        tool_def = MagicMock()
        tool_def.nango_provider_key = 'google-gmail'

        result = await check_abuse(
            redis_client=r,
            pool=pool,
            config=config,
            key_hash='abc123def456',
            user_id='user1',
            tool_def=tool_def,
        )

        assert result is None  # allowed

    @pytest.mark.asyncio
    async def test_check_abuse_blocks_daily_quota(self):
        r, pipe = _mock_redis(pipe_execute_result=[1])
        r.get.return_value = None

        pool = AsyncMock()
        row = MagicMock()
        row.__getitem__ = lambda self, key: 5000  # at quota
        pool.fetchrow = AsyncMock(return_value=row)

        config = MagicMock()
        config.rate_limit_per_minute = 60
        config.daily_quota = 5000
        config.provider_cap_per_hour = 200
        config.circuit_breaker_threshold = 0.5
        config.circuit_breaker_min_calls = 5
        config.circuit_breaker_window = 60
        config.circuit_breaker_cooldown = 120

        tool_def = MagicMock()
        tool_def.nango_provider_key = 'google-gmail'

        result = await check_abuse(
            redis_client=r,
            pool=pool,
            config=config,
            key_hash='abc123def456',
            user_id='user1',
            tool_def=tool_def,
        )

        assert result is not None
        assert result['success'] is False
        assert result['error']['code'] == 'QUOTA_EXCEEDED'
