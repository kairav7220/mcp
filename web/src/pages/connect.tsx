import { useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { toast } from 'sonner'
import { Copy } from 'lucide-react'

const GATEWAY_DEFAULT = import.meta.env.VITE_GATEWAY_URL ?? 'http://localhost:8000/mcp'

interface Snippet {
  name: string
  language: string
  code: string
}

function buildSnippets(gatewayUrl: string, apiKey: string): Snippet[] {
  const authHeader = `Bearer ${apiKey}`
  return [
    {
      name: 'opencode',
      language: 'json',
      code: JSON.stringify({
        mcp: {
          'mcp-hub': {
            type: 'remote',
            url: gatewayUrl,
            enabled: true,
            headers: { Authorization: authHeader },
          },
        },
      }, null, 2),
    },
    {
      name: 'Claude Desktop',
      language: 'json',
      code: JSON.stringify({
        mcpServers: {
          'mcp-hub': {
            url: gatewayUrl,
            transport: 'http',
            headers: { Authorization: authHeader },
          },
        },
      }, null, 2),
    },
    {
      name: 'Claude Code CLI',
      language: 'bash',
      code: `claude mcp add --transport http mcp-hub ${gatewayUrl} \\\n  --header "Authorization: ${authHeader}"`,
    },
    {
      name: 'Cursor / VS Code Copilot',
      language: 'json',
      code: JSON.stringify({
        mcpServers: {
          'mcp-hub': {
            url: gatewayUrl,
            headers: { Authorization: authHeader },
          },
        },
      }, null, 2),
    },
  ]
}

export function ConnectPage() {
  const [gatewayUrl, setGatewayUrl] = useState(GATEWAY_DEFAULT)
  const [apiKey, setApiKey] = useState('')
  const snippets = buildSnippets(gatewayUrl, apiKey)

  const copySnippet = (code: string, name: string) => {
    navigator.clipboard.writeText(code)
    toast.success(`Copied ${name} config`)
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Connect</h1>
      <p className="text-sm text-muted-foreground">
        Generate ready-to-paste configs for your favorite LLM client.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor="gateway-url">Gateway URL</Label>
          <Input
            id="gateway-url"
            placeholder={GATEWAY_DEFAULT}
            value={gatewayUrl}
            onChange={(e) => setGatewayUrl(e.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="api-key">API Key</Label>
          <Input
            id="api-key"
            placeholder="sk-..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>
      </div>

      <Separator />

      <div className="grid gap-4 lg:grid-cols-2">
        {snippets.map((s) => (
          <Card key={s.name}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">{s.name}</CardTitle>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => copySnippet(s.code, s.name)}
                  disabled={!apiKey}
                >
                  <Copy className="mr-1.5 size-3.5" />
                  Copy
                </Button>
              </div>
              <CardDescription>{s.language}</CardDescription>
            </CardHeader>
            <CardContent>
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs leading-relaxed">
                {s.code}
              </pre>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
