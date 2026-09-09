"""FastAPI dependencies — human auth (managed login) + admin guard.

Separate auth layers (§3.20): dashboard humans authenticate via Supabase JWT.
API keys can NEVER reach these routes (different token shape, rejected).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from .config import get_config
from .db import get_pool
from .security import AuthError, verify_supabase_jwt


async def get_current_user(request: Request) -> dict[str, Any]:
    """Bearer Supabase JWT -> users row (dict). 401 on anything else."""
    auth = request.headers.get('Authorization', '')
    parts = auth.split(' ', 1)
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == 'bearer' else ''
    if not token:
        raise HTTPException(status_code=401, detail='Missing bearer token')

    try:
        cfg = get_config()
        claims = verify_supabase_jwt(token, cfg.supabase_jwt_secret, supabase_url=cfg.supabase_url)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    supabase_id = claims.get('sub')
    email = claims.get('email') or ''
    pool = await get_pool()

    row = None
    if supabase_id:
        row = await pool.fetchrow('SELECT * FROM users WHERE supabase_id = $1', str(supabase_id))
    if row is None and email:
        # First login: link by email, store supabase_id
        row = await pool.fetchrow(
            "UPDATE users SET supabase_id = $1, updated_at = now() "
            "WHERE email = $2 AND status = 'active' RETURNING *",
            str(supabase_id), email,
        )
    if row is None:
        raise HTTPException(status_code=403, detail='No provisioned account for this identity')

    user = dict(row)
    if user['status'] != 'active':
        raise HTTPException(status_code=403, detail='Account disabled')
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Server-side role check — the UI button is decoration, not security."""
    if user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin role required')
    return user
