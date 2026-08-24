"""Tool registry — loads providers.json at startup and registers tools dynamically."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class ToolParam(BaseModel):
    type: str = 'string'
    description: str = ''
    required: bool = False
    enum: list[str] | None = None


class ToolDef(BaseModel):
    name: str
    provider: str
    nango_provider_key: str
    description: str
    method: str
    path: str
    params: dict[str, Any] = Field(default_factory=dict)


class Registry:
    """In-memory tool catalog loaded from providers.json."""

    def __init__(self) -> None:
        self.tools: dict[str, ToolDef] = {}

    def load_from_file(self, path: str | Path) -> int:
        """Load tools from providers.json. Returns tool count."""
        p = Path(path)
        if not p.exists():
            log.warning('providers.json not found at %s', p)
            return 0

        data = json.loads(p.read_text())
        count = 0

        for provider_key, provider_data in data.items():
            nango_key = provider_data.get('nango_provider_key', provider_key)
            tools_dict = provider_data.get('tools', {})

            for tool_name, tool_data in tools_dict.items():
                qualified_name = f'{provider_key}_{tool_name}'
                self.tools[qualified_name] = ToolDef(
                    name=qualified_name,
                    provider=provider_key,
                    nango_provider_key=nango_key,
                    description=tool_data.get('description', ''),
                    method=tool_data.get('method', 'GET'),
                    path=tool_data.get('path', ''),
                    params=tool_data.get('params', {}),
                )
                count += 1

        log.info('Loaded %d tools from %s', count, p.name)
        return count

    def get(self, tool_name: str) -> ToolDef | None:
        return self.tools.get(tool_name)

    def list_all(self) -> list[ToolDef]:
        return list(self.tools.values())

    def list_by_provider(self, provider: str) -> list[ToolDef]:
        return [t for t in self.tools.values() if t.provider == provider]

    def providers(self) -> list[str]:
        return sorted({t.provider for t in self.tools.values()})
