import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Summary, ProviderStat, ToolCall } from '@/lib/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { Activity, AlertTriangle, Users, KeyRound, Cable, Zap } from 'lucide-react'
import { CHART_COLORS } from '@/lib/chart-config'

function KpiCard({ icon: Icon, label, value, sub }: { icon: typeof Activity; label: string; value: string | number; sub?: string }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-muted">
          <Icon className="size-5 text-muted-foreground" />
        </div>
        <div>
          <p className="text-2xl font-semibold tabular-nums">{value}</p>
          <p className="text-sm text-muted-foreground">{label}</p>
          {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
        </div>
      </CardContent>
    </Card>
  )
}

function StatSkeleton() {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        <Skeleton className="size-10 rounded-md" />
        <div className="space-y-2">
          <Skeleton className="h-7 w-16" />
          <Skeleton className="h-4 w-24" />
        </div>
      </CardContent>
    </Card>
  )
}

export function OverviewPage() {
  const summary = useQuery<Summary>({ queryKey: ['summary'], queryFn: () => api('/api/v1/metrics/summary') })
  const providers = useQuery<ProviderStat[]>({ queryKey: ['providers'], queryFn: () => api('/api/v1/metrics/providers?days=7') })
  const errors = useQuery<ToolCall[]>({ queryKey: ['errors'], queryFn: () => api('/api/v1/metrics/errors?limit=10') })
  const lastSeen = useQuery<{ user_id: string; email: string; last_seen: string }[]>({ queryKey: ['last-seen'], queryFn: () => api('/api/v1/metrics/last-seen?limit=10') })

  const s = summary.data
  const errRate = s ? (s.error_rate_today * 100).toFixed(1) + '%' : '—'
  const isLoading = summary.isLoading

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {isLoading ? (
          Array.from({ length: 6 }).map((_, i) => <StatSkeleton key={i} />)
        ) : (
          <>
            <KpiCard icon={Zap} label="Calls Today" value={s?.calls_today ?? 0} />
            <KpiCard icon={AlertTriangle} label="Errors Today" value={s?.errors_today ?? 0} />
            <KpiCard icon={Activity} label="Error Rate" value={errRate} />
            <KpiCard icon={Users} label="Active Users (7d)" value={s?.active_users_7d ?? 0} />
            <KpiCard icon={KeyRound} label="Active Keys" value={s?.active_keys ?? 0} />
            <KpiCard icon={Cable} label="Connections" value={s?.active_connections ?? 0} />
          </>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Calls by Provider (7d)</CardTitle>
          </CardHeader>
          <CardContent>
            {providers.isLoading ? (
              <Skeleton className="h-[200px] w-full" />
            ) : providers.data && providers.data.length > 0 ? (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={providers.data} layout="vertical" margin={{ left: 100 }}>
                  <XAxis type="number" tick={{ fontSize: 12 }} />
                  <YAxis type="category" dataKey="provider" tick={{ fontSize: 12 }} width={90} />
                  <Tooltip />
                  <Bar dataKey="calls" radius={[0, 4, 4, 0]}>
                    {providers.data.map((_, i) => (
                      <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">No data yet</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Errors</CardTitle>
          </CardHeader>
          <CardContent>
            {errors.isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
              </div>
            ) : errors.data && errors.data.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Error</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {errors.data.map((call) => (
                    <TableRow key={call.id}>
                      <TableCell className="font-mono text-xs">{call.tool_name}</TableCell>
                      <TableCell>
                        <Badge variant="destructive" className="text-xs">{call.status_code ?? call.status}</Badge>
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">{call.error ?? '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">No errors</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Last Active Users</CardTitle>
        </CardHeader>
        <CardContent>
          {lastSeen.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : lastSeen.data && lastSeen.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Last Seen</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {lastSeen.data.map((u) => (
                  <TableRow key={u.user_id}>
                    <TableCell className="font-mono text-xs">{u.email}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(u.last_seen).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">No activity yet</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
