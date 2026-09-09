"""Security primitives — Supabase JWT verification + API key generation."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time

import httpx
import jwt

log = logging.getLogger(__name__)

# JWKS cache
_jwks_cache: dict = {'keys': None, 'fetched_at': 0.0}
_JWKS_TTL = 600  # 10 minutes


class AuthError(Exception):
    """Raised when a token is missing/invalid/expired. Maps to 401."""


def _fetch_jwks(supabase_url: str) -> list[dict]:
    """Fetch Supabase JWKS, cached for _JWKS_TTL seconds."""
    now = time.time()
    if _jwks_cache['keys'] and (now - _jwks_cache['fetched_at']) < _JWKS_TTL:
        return _jwks_cache['keys']
    try:
        r = httpx.get(f'{supabase_url}/auth/v1/.well-known/jwks.json', timeout=5)
        r.raise_for_status()
        keys = r.json().get('keys', [])
        _jwks_cache['keys'] = keys
        _jwks_cache['fetched_at'] = now
        return keys
    except Exception as e:
        log.warning('Failed to fetch JWKS: %s', e)
        return _jwks_cache['keys'] or []


def verify_supabase_jwt(token: str, jwt_secret: str, supabase_url: str = '') -> dict:
    """
    Verify a Supabase managed-login access token.
    Auto-detects algorithm from header: ES256 (new publishable keys) or HS256 (legacy).
    Returns the claims dict. Raises AuthError on any failure.
    """
    if not token:
        raise AuthError('Missing token')

    header = jwt.get_unverified_header(token)
    alg = header.get('alg', '')

    if alg == 'ES256':
        if not supabase_url:
            raise AuthError('SUPABASE_URL required for ES256 verification')
        kid = header.get('kid')
        keys = _fetch_jwks(supabase_url)
        for key_data in keys:
            if key_data.get('kid') == kid:
                try:
                    public_key = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(key_data))
                    return jwt.decode(token, public_key, algorithms=['ES256'],
                                      options={'verify_aud': False})
                except jwt.ExpiredSignatureError as e:
                    raise AuthError('Token expired') from e
                except jwt.InvalidTokenError as e:
                    log.warning('ES256 verification failed [%s]: %s', type(e).__name__, e)
                    raise AuthError(f'Invalid token: {e}') from e
        raise AuthError(f'No matching key for kid={kid}')

    # Legacy HS256
    if not jwt_secret:
        raise AuthError('Server not configured with SUPABASE_JWT_SECRET')
    try:
        return jwt.decode(token, jwt_secret, algorithms=['HS256'],
                          options={'verify_aud': False}, issuer='supabase')
    except jwt.ExpiredSignatureError as e:
        raise AuthError('Token expired') from e
    except jwt.InvalidTokenError as e:
        raise AuthError('Invalid token') from e


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key. Returns (raw_plaintext, sha256_hash, display_prefix)."""
    raw = f'sk-{secrets.token_urlsafe(32)}'
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:11]  # 'sk-' + first 8 chars
    return raw, key_hash, prefix
