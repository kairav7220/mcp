"""Provider catalog — built live from tool_registry DB (source of truth).

Replaces the old providers.json file snapshot: the Integrations page now
reflects whatever is actually in the database, including newly imported
providers, with no restart needed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx


async def load_catalog_from_db(
    pool: Any, nango_host: str = '', nango_secret: str = ''
) -> list[dict[str, Any]]:
    """Build provider catalog from tool_registry. One card per provider.

    Providers that differ only by trailing digits (apollo/apollo2) are merged
    into a single card, matching the old file-based behavior. Providers that
    exist in Nango but have no tools yet (e.g. youtube, google-docs) are
    appended as zero-tool OAuth cards so users can still connect them.
    """
    rows = await pool.fetch(
        """
        SELECT regexp_replace(provider, '\\d+$', '') AS base,
               MIN(provider) AS provider,
               COUNT(*) AS tool_count,
               MIN(COALESCE(input_schema->>'base_url', '')) AS base_url,
               MIN(COALESCE(input_schema->>'nango_provider_key', provider)) AS nango_provider_key,
               BOOL_OR(COALESCE(security_scheme->>'type', '') IN ('apiKey', 'http')) AS has_api_key
          FROM tool_registry
         WHERE enabled = true
         GROUP BY base
         ORDER BY COUNT(*) DESC, base
        """
    )
    catalog: list[dict[str, Any]] = []
    for r in rows:
        catalog.append({
            'provider': r['provider'],
            'nango_provider_key': r['nango_provider_key'] or r['provider'],
            'name': r['base'].replace('-', ' ').replace('_', ' ').title(),
            'description': '',
            'base_url': r['base_url'] or '',
            'logo_url': '',
            'tool_count': r['tool_count'],
            'category': '',
            'auth_type': 'api_key' if r['has_api_key'] else 'oauth',
        })

    # Append Nango integrations that have no tools yet (connect-only cards)
    if nango_host and nango_secret:
        try:
            seen = {c['provider'] for c in catalog} | {c['nango_provider_key'] for c in catalog}
            bases = {re.sub(r'\d+$', '', c['provider']) for c in catalog}
            resp = httpx.get(
                f'{nango_host}/integrations',
                headers={'Authorization': f'Bearer {nango_secret}'},
                timeout=10.0,
            )
            resp.raise_for_status()
            for integ in resp.json().get('data', []):
                key = integ['unique_key']
                if key in seen:
                    continue
                if key.startswith('google-') and key[7:] in bases:
                    continue  # covered by an existing card (e.g. Drive via google-gmail)
                seen.add(key)
                catalog.append({
                    'provider': key,
                    'nango_provider_key': key,
                    'name': integ.get('display_name', key.replace('-', ' ').title()),
                    'description': '',
                    'base_url': '',
                    'logo_url': '',
                    'tool_count': 0,
                    'category': '',
                    'auth_type': 'oauth',
                })
        except Exception:
            pass

    return catalog


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
