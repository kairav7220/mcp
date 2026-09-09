import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { ToolCall } from '@/lib/types'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'

export function LogsPage() {
  const [provider, setProvider] = useState('')
  const [status, setStatus] = useState('')

  const queryKey = ['calls', provider, status]
  const calls = useQuery<ToolCall[]>({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams({ limit: '100' })
      if (provider) params.set('provider', provider)
      if (status) params.set('status', status)
      return api(`/api/v1/metrics/calls?${params}`)
    },
  })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Logs</h1>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
            <div className="grid gap-2">
              <Label>Provider</Label>
              <Input
                placeholder="e.g. google-gmail"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="w-48"
              />
            </div>
            <div className="grid gap-2">
              <Label>Status</Label>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger className="w-36"><SelectValue placeholder="All" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="success">Success</SelectItem>
                  <SelectItem value="error">Error</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setProvider(''); setStatus('') }}
            >
              Clear
            </Button>
          </div>
          <CardDescription>Recent tool calls</CardDescription>
        </CardHeader>
        <CardContent>
          {calls.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : calls.data && calls.data.length > 0 ? (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead>Provider</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Code</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead>Error</TableHead>
                    <TableHead>Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {calls.data.map((call) => (
                    <TableRow key={call.id}>
                      <TableCell className="font-mono text-xs">{call.tool_name}</TableCell>
                      <TableCell className="text-xs">{call.provider}</TableCell>
                      <TableCell>
                        <Badge variant={call.status === 'success' ? 'default' : 'destructive'} className="text-xs">
                          {call.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs tabular-nums">{call.status_code ?? '—'}</TableCell>
                      <TableCell className="text-xs tabular-nums">{call.duration_ms != null ? `${call.duration_ms}ms` : '—'}</TableCell>
                      <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">
                        {call.error ?? '—'}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                        {new Date(call.created_at).toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">No logs yet</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
