// Shared types mirroring control-plane response schemas.

export interface Profile {
  id: string
  email: string
  name: string
  role: 'admin' | 'member'
  status: 'active' | 'disabled'
}

export interface Summary {
  calls_today: number
  errors_today: number
  error_rate_today: number
  calls_7d: number
  active_users_7d: number
  active_keys: number
  active_connections: number
}

export interface ProviderStat {
  provider: string
  calls: number
  errors: number
  total_duration_ms: number
  error_rate: number
}

export interface CatalogItem {
  provider: string
  nango_provider_key: string
  name: string
  description: string
  base_url: string
  logo_url: string
  tool_count: number
  category: string
  auth_type: 'oauth' | 'api_key'
}

export interface UserConnection {
  user_id: string
  provider: string
  nango_connection_id: string
  status: string
  created_at: string
  updated_at: string
}

export interface ApiKey {
  id: string
  name: string
  key_prefix: string
  scopes: string[]
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface ApiKeyCreated extends ApiKey {
  plaintext: string
}

export interface ManagedUser {
  id: string
  email: string
  name: string
  role: 'admin' | 'member'
  status: 'active' | 'disabled'
  created_at: string
  updated_at: string
}

export interface ToolCall {
  id: string
  user_id: string
  api_key_id: string | null
  tool_name: string
  provider: string
  status: string
  duration_ms: number | null
  status_code: number | null
  error: string | null
  created_at: string
  user_email: string | null
}

export interface ConnectSession {
  user_id: string
  token: string
  expires_at: string | null
  connect_url: string | null
}

export interface RegistryTool {
  id: string
  provider: string
  name: string
  description: string
  method: string
  path: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown> | null
  required_scopes: string[]
  enabled: boolean
  public: boolean
  tags: string[]
  version: number
  created_at: string
  updated_at: string
}
