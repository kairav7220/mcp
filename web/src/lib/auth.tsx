import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from '@/lib/supabase'
import { api, bindSessionGetter } from '@/lib/api'
import type { Profile } from '@/lib/types'

interface AuthState {
  session: Session | null
  profile: Profile | null
  loading: boolean
}

const AuthContext = createContext<AuthState>({ session: null, profile: null, loading: true })

async function fetchProfile(): Promise<Profile | null> {
  try {
    return await api<Profile>('/api/v1/me')
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ session: null, profile: null, loading: true })

  useEffect(() => {
    let active = true

    const init = async () => {
      const { data: { session } } = await supabase.auth.getSession()
      if (!active) return
      bindSessionGetter(async () => session)

      const profile = session ? await fetchProfile() : null
      if (!active) return
      setState({ session, profile, loading: false })
    }

    init()

    const { data: { subscription } } = supabase.auth.onAuthStateChange(async (_event, session) => {
      if (!active) return
      bindSessionGetter(async () => session)
      const profile = session ? await fetchProfile() : null
      if (!active) return
      setState((prev) => ({ ...prev, session, profile }))
    })

    return () => {
      active = false
      subscription.unsubscribe()
    }
  }, [])

  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
