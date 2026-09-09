import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

export interface UserApiKey {
  provider: string
  has_key: boolean
  created_at: string | null
}

export function useUserApiKeys() {
  return useQuery<UserApiKey[]>({
    queryKey: ['user-api-keys'],
    queryFn: () => api('/api/v1/user-api-keys/'),
  })
}

export function useSaveApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ provider, api_key }: { provider: string; api_key: string }) =>
      api('/api/v1/user-api-keys/', {
        method: 'POST',
        body: JSON.stringify({ provider, api_key }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user-api-keys'] }),
  })
}

export function useDeleteApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (provider: string) =>
      api(`/api/v1/user-api-keys/${provider}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user-api-keys'] }),
  })
}
