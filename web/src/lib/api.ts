import type { Session } from '@supabase/supabase-js'

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8001'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let sessionGetter: () => Promise<Session | null> = async () => null

/** Wired by the AuthProvider so every request carries the current access token. */
export function bindSessionGetter(fn: () => Promise<Session | null>) {
  sessionGetter = fn
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const session = await sessionGetter()
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
      ...init.headers,
    },
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // keep statusText
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}
