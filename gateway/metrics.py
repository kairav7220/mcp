"""Metering — writes tool_call records and daily metric rollups."""

from __future__ import annotations

import logging
from datetime import date

import asyncpg

log = logging.getLogger(__name__)


async def record_tool_call(
    pool: asyncpg.Pool,
    *,
    user_id: str,
    api_key_id: str | None,
    tool_name: str,
    provider: str,
    status: str,
    duration_ms: int,
    status_code: int | None = None,
    error: str | None = None,
) -> None:
    """Write a single tool_call row and upsert the daily metric."""
    today = date.today()

    await pool.execute(
        """
        INSERT INTO tool_calls (user_id, api_key_id, tool_name, provider, status, duration_ms, status_code, error)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        user_id,
        api_key_id,
        tool_name,
        provider,
        status,
        duration_ms,
        status_code,
        error,
    )

    # Upsert daily metric
    is_error = 1 if status == "error" else 0
    await pool.execute(
        """
        INSERT INTO metric_daily (date, user_id, provider, tool_name, calls, errors, total_duration_ms)
        VALUES ($1, $2, $3, $4, 1, $5, $6)
        ON CONFLICT (date, user_id, provider, tool_name) DO UPDATE SET
            calls = metric_daily.calls + 1,
            errors = metric_daily.errors + EXCLUDED.errors,
            total_duration_ms = metric_daily.total_duration_ms + EXCLUDED.total_duration_ms
        """,
        today,
        user_id,
        provider,
        tool_name,
        is_error,
        duration_ms,
    )


async def get_daily_usage(pool: asyncpg.Pool, user_id: str) -> int:
    """Return today's total call count for a user."""
    today = date.today()
    row = await pool.fetchrow(
        'SELECT COALESCE(SUM(calls), 0) AS total FROM metric_daily WHERE date = $1 AND user_id = $2',
        today,
        user_id,
    )
    return row['total'] if row else 0
