import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from '@/lib/auth'
import { RequireAuth, RequireAdmin, Shell } from '@/components/app/shell'
import { LoginPage } from '@/pages/login'
import { OverviewPage } from '@/pages/overview'
import { IntegrationsPage } from '@/pages/integrations'
import { ConnectPage } from '@/pages/connect'
import { ApiKeysPage } from '@/pages/api-keys'
import { UsersPage } from '@/pages/users'
import { LogsPage } from '@/pages/logs'
import { ToolsPage } from '@/pages/tools'
import { Toaster } from '@/components/ui/sonner'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<RequireAuth />}>
              <Route element={<Shell />}>
                <Route path="/overview" element={<OverviewPage />} />
                <Route path="/integrations" element={<IntegrationsPage />} />
                <Route path="/connect" element={<ConnectPage />} />
                <Route path="/api-keys" element={<ApiKeysPage />} />
                <Route element={<RequireAdmin />}>
                  <Route path="/tools" element={<ToolsPage />} />
                  <Route path="/users" element={<UsersPage />} />
                  <Route path="/logs" element={<LogsPage />} />
                </Route>
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </BrowserRouter>
        <Toaster richColors position="top-right" />
      </AuthProvider>
    </QueryClientProvider>
  )
}
