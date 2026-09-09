"""Provider catalog — merge providers.json + Nango integrations."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx


def load_catalog(path: str | Path, nango_host: str = '', nango_secret: str = '') -> list[dict[str, Any]]:
    """Build catalog from providers.json + Nango integrations API."""
    seen: set[str] = set()
    catalog: list[dict[str, Any]] = []

    # 1. Load from providers.json
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            data = {}
        for provider_key, pdata in data.items():
            base = re.sub(r'\d+$', '', provider_key)
            if base in seen:
                continue
            seen.add(base)
            tools = pdata.get('tools', {})
            catalog.append({
                'provider': provider_key,
                'nango_provider_key': pdata.get('nango_provider_key', provider_key),
                'name': base.replace('-', ' ').title(),
                'base_url': pdata.get('base_url', ''),
                'tool_count': len(tools),
                'auth_type': 'oauth',
            })

    # 2. Fetch from Nango integrations API and add any missing
    if nango_host and nango_secret:
        try:
            resp = httpx.get(
                f'{nango_host}/integrations',
                headers={'Authorization': f'Bearer {nango_secret}'},
                timeout=10.0,
            )
            resp.raise_for_status()
            # Build set of providers.json keys and their base names for dedup
            providers_json_bases = {c['provider'] for c in catalog}
            for integ in resp.json().get('data', []):
                key = integ['unique_key']
                if key in seen:
                    continue
                if key.startswith('google-') and key[7:] in providers_json_bases:
                    continue
                seen.add(key)
                catalog.append({
                    'provider': key,
                    'nango_provider_key': key,
                    'name': integ.get('display_name', key.replace('-', ' ').title()),
                    'base_url': '',
                    'tool_count': 0,
                    'auth_type': 'oauth',
                })
        except Exception:
            pass

    catalog.sort(key=lambda c: (-c['tool_count'], c['provider']))

    # 3. Add API-key based providers (not in Nango)
    if not any(c['provider'] == 'google-maps' for c in catalog):
        catalog.append({
            'provider': 'google-maps',
            'nango_provider_key': 'google-maps',
            'name': 'Google Maps',
            'base_url': 'https://maps.googleapis.com',
            'tool_count': 5,
            'auth_type': 'api_key',
        })

    catalog.sort(key=lambda c: (-c['tool_count'], c['provider']))
    return catalog


def ensure_importable() -> None:
    """Allow running control-plane standalone with gateway on sys.path."""
    root = str(Path(__file__).resolve().parent.parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
