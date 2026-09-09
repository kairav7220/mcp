import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import type { ApiKey, ApiKeyCreated } from '@/lib/types'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import { toast } from 'sonner'
import { Plus, Copy, Trash2 } from 'lucide-react'

export function ApiKeysPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [newKeyScopes, setNewKeyScopes] = useState('')
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null)

  const keys = useQuery<ApiKey[]>({ queryKey: ['api-keys'], queryFn: () => api('/api/v1/api-keys') })

  const createKey = useMutation({
    mutationFn: () =>
      api<ApiKeyCreated>('/api/v1/api-keys', {
        method: 'POST',
        body: JSON.stringify({
          name: newKeyName,
          scopes: newKeyScopes ? newKeyScopes.split(',').map((s) => s.trim()) : [],
        }),
      }),
    onSuccess: (data) => {
      setCreatedKey(data)
      setNewKeyName('')
      setNewKeyScopes('')
      setCreateOpen(false)
      queryClient.invalidateQueries({ queryKey: ['api-keys'] })
      toast.success('API key created')
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : 'Failed to create key')
    },
  })

  const revokeKey = useMutation({
    mutationFn: (keyId: string) => api(`/api/v1/api-keys/${keyId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setRevokeTarget(null)
      queryClient.invalidateQueries({ queryKey: ['api-keys'] })
      toast.success('Key revoked')
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : 'Failed to revoke key')
    },
  })

  const copyKey = (plaintext: string) => {
    navigator.clipboard.writeText(plaintext)
    toast.success('Copied to clipboard')
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">API Keys</h1>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="mr-1.5 size-4" />
          Create Key
        </Button>
      </div>

      {createdKey && (
        <Alert>
          <AlertTitle>New API key — copy it now, it won't be shown again</AlertTitle>
          <AlertDescription className="mt-2 flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded bg-muted px-2 py-1 text-sm font-mono">
              {createdKey.plaintext}
            </code>
            <Button size="sm" variant="outline" onClick={() => copyKey(createdKey.plaintext)}>
              <Copy className="size-3.5" />
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setCreatedKey(null)}>
              Dismiss
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your Keys</CardTitle>
          <CardDescription>Manage API keys for MCP gateway access</CardDescription>
        </CardHeader>
        <CardContent>
          {keys.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
            </div>
          ) : keys.data && keys.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Prefix</TableHead>
                  <TableHead>Scopes</TableHead>
                  <TableHead>Last Used</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-12" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.data.map((k) => (
                  <TableRow key={k.id}>
                    <TableCell className="font-medium">{k.name}</TableCell>
                    <TableCell className="font-mono text-xs">{k.key_prefix}…</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        {k.scopes.map((scope) => (
                          <Badge key={scope} variant="outline" className="text-xs">{scope}</Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {k.last_used_at ? new Date(k.last_used_at).toLocaleDateString() : 'Never'}
                    </TableCell>
                    <TableCell>
                      <Badge variant={k.revoked_at ? 'destructive' : 'default'}>
                        {k.revoked_at ? 'Revoked' : 'Active'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {!k.revoked_at && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setRevokeTarget(k)}
                          className="text-destructive hover:text-destructive"
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">No API keys yet</p>
          )}
        </CardContent>
      </Card>

      {/* Create Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create API Key</DialogTitle>
            <DialogDescription>Create a new key for gateway access. The key will only be shown once.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="key-name">Name</Label>
              <Input id="key-name" placeholder="e.g. my-laptop" value={newKeyName} onChange={(e) => setNewKeyName(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="key-scopes">Scopes (comma-separated, optional)</Label>
              <Input id="key-scopes" placeholder="e.g. gmail, slack" value={newKeyScopes} onChange={(e) => setNewKeyScopes(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button onClick={() => createKey.mutate()} disabled={createKey.isPending || !newKeyName}>
              {createKey.isPending ? 'Creating...' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke Confirmation */}
      <AlertDialog open={!!revokeTarget} onOpenChange={() => setRevokeTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke API key?</AlertDialogTitle>
            <AlertDialogDescription>
              This will immediately invalidate <strong>{revokeTarget?.name}</strong>. Any services using this key will lose access.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => revokeTarget && revokeKey.mutate(revokeTarget.id)} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
