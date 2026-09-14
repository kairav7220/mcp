"""Metrics — read-only observability over tool_calls / metric_daily."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..db import get_pool
from ..deps import require_admin

router = APIRouter(prefix='/api/v1/metrics', tags=['metrics'])


@router.get('/summary')
async def summary(_: dict = Depends(require_admin)) -> dict:
    pool = await get_pool()
    today = date.today()
    week_ago = today - timedelta(days=7)

    calls_today, errors_today = await pool.fetchrow(
        'SELECT COALESCE(SUM(calls),0), COALESCE(SUM(errors),0) FROM metric_daily WHERE date = $1',
        today,
    )
    week_row = await pool.fetchrow(
        'SELECT COALESCE(SUM(calls),0), COUNT(DISTINCT user_id) FROM metric_daily WHERE date >= $1',
        week_ago,
    )
    active_keys = await pool.fetchval('SELECT count(*) FROM api_keys WHERE revoked_at IS NULL')
    active_conns = await pool.fetchval("SELECT count(*) FROM user_connections WHERE status = 'active'")

    calls_today_n, errors_today_n = int(calls_today), int(errors_today)
    error_rate = errors_today_n / calls_today_n if calls_today_n else 0.0

    return {
        'calls_today': calls_today_n,
        'errors_today': errors_today_n,
        'error_rate_today': round(error_rate, 4),
        'calls_7d': int(week_row[0]),
        'active_users_7d': int(week_row[1]),
        'active_keys': int(active_keys),
        'active_connections': int(active_conns),
    }


@router.get('/calls')
async def calls(
    from_: datetime | None = Query(None, alias='from'),
    to: datetime | None = None,
    user_id: UUID | None = None,
    provider: str | None = None,
    status: str | None = None,
    limit: int = Query(50, le=500),
    _: dict = Depends(require_admin),
) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT tc.id, tc.user_id, tc.api_key_id, tc.tool_name, tc.provider,
               tc.status, tc.duration_ms, tc.status_code, tc.error, tc.created_at,
               u.email AS user_email
        FROM tool_calls tc LEFT JOIN users u ON u.id = tc.user_id
        WHERE ($1::timestamptz IS NULL OR tc.created_at >= $1)
          AND ($2::timestamptz IS NULL OR tc.created_at <= $2)
          AND ($3::uuid IS NULL OR tc.user_id = $3)
          AND ($4::text IS NULL OR tc.provider = $4)
          AND ($5::text IS NULL OR tc.status = $5)
        ORDER BY tc.created_at DESC LIMIT $6
        """,
        from_, to, user_id, provider, status, limit,
    )
    out = []
    for r in rows:
        d = dict(r)
        d['id'] = str(d['id'])
        d['user_id'] = str(d['user_id'])
        d['api_key_id'] = str(d['api_key_id']) if d['api_key_id'] else None
        out.append(d)
    return out


@router.get('/errors')
async def recent_errors(limit: int = Query(50, le=200), _: dict = Depends(require_admin)) -> list[dict]:
    """Recent failures — the dashboard's 'recent failures' feed."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT tc.id, tc.tool_name, tc.provider, tc.error, tc.status_code,
               tc.created_at, u.email AS user_email
        FROM tool_calls tc LEFT JOIN users u ON u.id = tc.user_id
        WHERE tc.status = 'error'
        ORDER BY tc.created_at DESC LIMIT $1
        """,
        limit,
    )
    return [{**dict(r), 'id': str(r['id'])} for r in rows]


@router.get('/providers')
async def providers(
    days: int = Query(7, ge=1, le=90),
    _: dict = Depends(require_admin),
) -> list[dict]:
    """Per-provider breakdown over the last N days."""
    pool = await get_pool()
    since = date.today() - timedelta(days=days - 1)
    rows = await pool.fetch(
        """
        SELECT provider,
               SUM(calls) AS calls,
               SUM(errors) AS errors,
               SUM(total_duration_ms) AS total_duration_ms,
               CASE WHEN SUM(calls) > 0 THEN ROUND(SUM(errors)::numeric / SUM(calls), 4) ELSE 0 END AS error_rate
        FROM metric_daily WHERE date >= $1
        GROUP BY provider ORDER BY calls DESC
        """,
        since,
    )
    return [dict(r) for r in rows]


@router.get('/last-seen')
async def last_seen(limit: int = Query(25, le=100), _: dict = Depends(require_admin)) -> list[dict]:
    """Users ordered by most recent activity."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT u.id, u.email, u.name, u.status, MAX(tc.created_at) AS last_seen,
               COUNT(tc.id) AS total_calls
        FROM users u LEFT JOIN tool_calls tc ON tc.user_id = u.id
        GROUP BY u.id, u.email, u.name, u.status
        ORDER BY last_seen DESC NULLS LAST LIMIT $1
        """,
        limit,
    )
    return [{**dict(r), 'id': str(r['id'])} for r in rows]
