"""Auth router — managed-login token verification."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import get_config
from ..db import get_pool
from ..deps import get_current_user
from ..schemas import ProfileResponse, TokenRequest
from ..security import AuthError, verify_supabase_jwt

router = APIRouter(prefix='/api/v1', tags=['auth'])


@router.post('/auth')
async def verify_token(req: TokenRequest) -> ProfileResponse:
    """Verify a Supabase access_token; returns our profile if provisioned."""
    try:
        cfg = get_config()
        claims = verify_supabase_jwt(req.access_token, cfg.supabase_jwt_secret, supabase_url=cfg.supabase_url)
    except AuthError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail=str(e)) from e

    pool = await get_pool()
    supabase_id = str(claims.get('sub') or '')
    email = claims.get('email') or ''

    row = await pool.fetchrow('SELECT * FROM users WHERE supabase_id = $1', supabase_id) if supabase_id else None
    if row is None and email:
        row = await pool.fetchrow(
            "UPDATE users SET supabase_id = $1, updated_at = now() "
            "WHERE email = $2 AND status = 'active' RETURNING *",
            supabase_id, email,
        )
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail='No provisioned account for this identity')

    user = dict(row)
    if user['status'] != 'active':
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail='Account disabled')

    return ProfileResponse(
        id=str(user['id']), email=user['email'], name=user['name'],
        role=user['role'], status=user['status'],
    )


@router.get('/me')
async def me(user: dict = Depends(get_current_user)) -> ProfileResponse:
    return ProfileResponse(
        id=str(user['id']),
        email=user['email'],
        name=user['name'],
        role=user['role'],
        status=user['status'],
    )
