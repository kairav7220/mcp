"""Control plane DB — asyncpg pool + shared schema (same Postgres as gateway)."""

from __future__ import annotations

import asyncpg

from .config import get_config

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(get_config().database_url)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def apply_schema() -> None:
    """Apply schema v1 (same DDL as gateway — idempotent CREATE IF NOT EXISTS)."""
    pool = await get_pool()
    import sys
    sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..', '..'))
    from gateway.db import SCHEMA_SQL
    await pool.execute(SCHEMA_SQL)


# ── user_connections read-model sync ─────────────────────────────────────────

UPSERT_CONNECTION_SQL = """
INSERT INTO user_connections (user_id, provider, nango_connection_id, status, updated_at)
VALUES ($1, $2, $3, $4, now())
ON CONFLICT (nango_connection_id) DO UPDATE SET
    user_id = EXCLUDED.user_id,
    provider = EXCLUDED.provider,
    status = EXCLUDED.status,
    updated_at = now()
"""


async def upsert_connection(user_id: str, provider: str, nango_connection_id: str, status: str) -> None:
    pool = await get_pool()
    # Two steps in one transaction: a reconnect arrives with a NEW nango id for
    # the same (user, provider), so the old row must go first. (A single
    # DELETE+INSERT statement trips the UNIQUE(user_id, provider) check
    # because CTE and INSERT share a snapshot.)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                'DELETE FROM user_connections WHERE user_id = $1 AND provider = $2 '
                'AND nango_connection_id <> $3',
                user_id, provider, nango_connection_id,
            )
            await conn.execute(UPSERT_CONNECTION_SQL, user_id, provider, nango_connection_id, status)


async def set_connection_status(nango_connection_id: str, status: str) -> None:
    pool = await get_pool()
    await pool.execute(
        'UPDATE user_connections SET status = $2, updated_at = now() WHERE nango_connection_id = $1',
        nango_connection_id,
        status,
    )


async def delete_connection(nango_connection_id: str) -> bool:
    pool = await get_pool()
    tag = await pool.execute(
        'DELETE FROM user_connections WHERE nango_connection_id = $1',
        nango_connection_id,
    )
    return tag == 'DELETE 1'


async def find_user_id_by_email(email: str) -> str | None:
    pool = await get_pool()
    row = await pool.fetchrow('SELECT id FROM users WHERE email = $1', email)
    return str(row['id']) if row else None


async def ensure_user(email: str, name: str | None = None) -> str:
    """Get or create a member user by email. Returns user_id."""
    pool = await get_pool()
    row = await pool.fetchrow('SELECT id FROM users WHERE email = $1', email)
    if row:
        return str(row['id'])
    row = await pool.fetchrow(
        "INSERT INTO users (email, name, role, status) VALUES ($1, $2, 'member', 'active') RETURNING id",
        email,
        name,
    )
    return str(row['id'])
