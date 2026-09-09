import { useState } from 'react'

const THESVG_CDN = 'https://thesvg.org/icons'
const LOGO_DEV_CDN = 'https://img.logo.dev'
const LOGO_DEV_TOKEN = import.meta.env.VITE_LOGO_DEV_TOKEN ?? ''

// Provider key → thesvg slug (only when slug differs from key)
const THESVG_SLUGS: Record<string, string> = {
  gmail: 'gmail-2026',
  drive: 'google-drive-2026',
  calendar: 'google-calendar-2026',
  'google-docs': 'google-docs-2026',
  'google-maps': 'google-maps',
}

// Icons where default variant is white (invisible on light bg) — use light variant instead
const USE_LIGHT_VARIANT = new Set(['firecrawl'])

function thesvgUrl(provider: string) {
  const slug = THESVG_SLUGS[provider] ?? provider
  const variant = USE_LIGHT_VARIANT.has(provider) ? 'light' : 'default'
  return `${THESVG_CDN}/${slug}/${variant}.svg`
}

function logoDevUrl(base_url: string) {
  if (!LOGO_DEV_TOKEN || !base_url) return ''
  try {
    const host = new URL(base_url).hostname.replace(/^www\./, '')
    return `${LOGO_DEV_CDN}/${host}?token=${LOGO_DEV_TOKEN}&size=128&format=png`
  } catch {
    return ''
  }
}

interface ProviderIconProps {
  name: string
  provider: string
  baseUrl?: string
  size?: 'sm' | 'md' | 'lg'
}

const SIZE = { sm: 'size-8 text-xs', md: 'size-10 text-sm', lg: 'size-12 text-base' } as const

export function ProviderIcon({ name, provider, baseUrl, size = 'md' }: ProviderIconProps) {
  const [stage, setStage] = useState<'thesvg' | 'logodev' | 'letter'>('thesvg')
  const initial = name.charAt(0).toUpperCase()

  if (stage === 'thesvg') {
    return (
      <img
        src={thesvgUrl(provider)}
        alt={`${name} logo`}
        className={`${SIZE[size]} shrink-0 rounded-lg`}
        style={{ objectFit: 'contain', maxWidth: '100%', maxHeight: '100%' }}
        onError={() => setStage('logodev')}
      />
    )
  }

  if (stage === 'logodev') {
    const fallback = logoDevUrl(baseUrl ?? '')
    if (fallback) {
      return (
        <img
          src={fallback}
          alt={`${name} logo`}
          className={`${SIZE[size]} shrink-0 rounded-lg`}
          style={{ objectFit: 'contain', maxWidth: '100%', maxHeight: '100%' }}
          onError={() => setStage('letter')}
        />
      )
    }
  }

  return (
    <div className={`flex ${SIZE[size]} shrink-0 items-center justify-center rounded-lg bg-muted font-semibold text-muted-foreground`}>
      {initial}
    </div>
  )
}
