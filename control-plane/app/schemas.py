"""Pydantic request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# ── Auth ─────────────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    access_token: str


class ProfileResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    status: str


# ── Users (admin) ────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    name: str = ''
    role: Literal['admin', 'member'] = 'member'


class UserPatch(BaseModel):
    name: str | None = None
    role: Literal['admin', 'member'] | None = None
    status: Literal['active', 'disabled'] | None = None


# ── API keys ────────────────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = []
    expires_at: str | None = None  # ISO timestamp, optional


class ApiKeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: str | None
    expires_at: str | None
    revoked_at: str | None
    created_at: str


class ApiKeyCreated(ApiKeyOut):
    plaintext: str  # shown ONCE
