"""Tool registry — loads tools from tool_registry DB (Phase 6+)."""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class ToolDef(BaseModel):
    name: str
    provider: str
    nango_provider_key: str
    description: str
    method: str
    path: str
    base_url: str = ''
    params: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    required_scopes: list[str] = Field(default_factory=list)
    security_scheme: dict[str, Any] | None = None
    enabled: bool = True
    public: bool = False
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    registry_id: str | None = None  # UUID from tool_registry table


class Registry:
    """In-memory tool catalog. Loads from tool_registry DB."""

    def __init__(self) -> None:
        self.tools: dict[str, ToolDef] = {}

    # ── DB loading ──────────────────────────────────────────────────────────

    async def load_from_db(self, pool: asyncpg.Pool) -> int:
        """Load enabled tools from tool_registry table. Returns tool count."""
        rows = await pool.fetch(
            'SELECT id, provider, name, description, method, path, '
            'input_schema, output_schema, required_scopes, security_scheme, public, tags, version '
            'FROM tool_registry WHERE enabled = true'
        )
        self.tools.clear()
        count = 0

        for row in rows:
            input_schema = json.loads(row['input_schema']) if isinstance(row['input_schema'], str) else (row['input_schema'] or {})
            output_schema = (
                json.loads(row['output_schema']) if isinstance(row['output_schema'], str) else row['output_schema']
            ) if row['output_schema'] else None

            nango_provider_key = (input_schema.get('nango_provider_key') or row['provider'])

            security_scheme = None
            if row['security_scheme']:
                security_scheme = json.loads(row['security_scheme']) if isinstance(row['security_scheme'], str) else row['security_scheme']

            self.tools[row['name']] = ToolDef(
                name=row['name'],
                provider=row['provider'],
                nango_provider_key=nango_provider_key,
                description=row['description'] or '',
                method=row['method'],
                path=row['path'],
                base_url=input_schema.get('base_url', ''),
                params=input_schema.get('params', {}),
                input_schema=input_schema,
                output_schema=output_schema,
                required_scopes=list(row['required_scopes'] or []),
                security_scheme=security_scheme,
                public=row['public'],
                tags=list(row['tags'] or []),
                version=row['version'],
                registry_id=str(row['id']),
            )
            count += 1

        log.info('Loaded %d enabled tools from tool_registry', count)
        return count

    # ── Lookup helpers ───────────────────────────────────────────────────────

    def get(self, tool_name: str) -> ToolDef | None:
        return self.tools.get(tool_name)

    def list_all(self) -> list[ToolDef]:
        return list(self.tools.values())

    def list_by_provider(self, provider: str) -> list[ToolDef]:
        return [t for t in self.tools.values() if t.provider == provider]

    def providers(self) -> list[str]:
        return sorted({t.provider for t in self.tools.values()})
