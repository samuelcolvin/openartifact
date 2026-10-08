/**
 * The MCP dialog: how to connect a coding agent or a chat platform to this server's MCP endpoint, one snippet
 * per client, and, on an artifact, a prompt to paste into the agent so it picks the artifact up and works on it
 * over MCP. What the snippets say comes from `/api/configure`: the endpoint URL, and whether a client signs in
 * with Google or sends the development token.
 */

import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { type Artifact, api, type McpInfo } from '../api.ts'
import { Button, Dialog, Spinner } from './ui.tsx'

type Tab = 'prompt' | 'setup'

interface Client {
  id: string
  name: string
  /** The snippet, and where it goes; `token` is set when the server takes the development token. */
  snippet: (mcp: McpInfo) => { text: string; where: string; language: 'shell' | 'json' | 'toml' | 'text' }
}

const AUTH_HEADER = (mcp: McpInfo) => `Authorization: Bearer ${mcp.token}`

const CLIENTS: Client[] = [
  {
    id: 'claude-code',
    name: 'Claude Code',
    snippet: (mcp) => ({
      language: 'shell',
      where: 'Run in a terminal, then /mcp inside Claude Code to check it is connected.',
      text:
        mcp.auth === 'token'
          ? `claude mcp add --transport http openartifact ${mcp.url} --header "${AUTH_HEADER(mcp)}"`
          : `claude mcp add --transport http openartifact ${mcp.url}`,
    }),
  },
  {
    id: 'codex',
    name: 'Codex',
    snippet: (mcp) => ({
      language: 'toml',
      where: 'Add to ~/.codex/config.toml, then restart Codex.',
      text:
        mcp.auth === 'token'
          ? `[mcp_servers.openartifact]\nurl = "${mcp.url}"\nhttp_headers = { Authorization = "Bearer ${mcp.token}" }`
          : `[mcp_servers.openartifact]\nurl = "${mcp.url}"\n\n# then sign in: codex mcp login openartifact`,
    }),
  },
  {
    id: 'cursor',
    name: 'Cursor',
    snippet: (mcp) => ({
      language: 'json',
      where: 'Add to .cursor/mcp.json in the project, or ~/.cursor/mcp.json for every project.',
      text: JSON.stringify({ mcpServers: { openartifact: server(mcp) } }, null, 2),
    }),
  },
  {
    id: 'vscode',
    name: 'VS Code',
    snippet: (mcp) => ({
      language: 'json',
      where: 'Add to .vscode/mcp.json in the project, then Start the server from the MCP view.',
      text: JSON.stringify({ servers: { openartifact: { type: 'http', ...server(mcp) } } }, null, 2),
    }),
  },
  {
    id: 'claude',
    name: 'Claude',
    snippet: (mcp) => ({
      language: 'text',
      where: 'Claude on the web and the desktop app, as a custom connector.',
      text:
        `Settings > Connectors > Add custom connector\n` +
        `Name: OpenArtifact\n` +
        `URL: ${mcp.url}\n` +
        `Add, then Connect and sign in with Google.` +
        (mcp.auth === 'token' ? `\n\n${PUBLIC_NOTE}` : ''),
    }),
  },
  {
    id: 'chatgpt',
    name: 'ChatGPT',
    snippet: (mcp) => ({
      language: 'text',
      where: 'ChatGPT on the web, as a connector in developer mode.',
      text:
        `Settings > Apps & Connectors > Advanced settings > Developer mode: on\n` +
        `Settings > Apps & Connectors > Create\n` +
        `Name: OpenArtifact\n` +
        `MCP server URL: ${mcp.url}\n` +
        `Authentication: OAuth\n` +
        `Create, then sign in with Google when asked.` +
        (mcp.auth === 'token' ? `\n\n${PUBLIC_NOTE}` : ''),
    }),
  },
]

const PUBLIC_NOTE =
  'This server is on a development token, which the platform cannot send: it needs a deployment with Google ' +
  'login configured and reachable from the internet.'

/** The server entry the JSON-configured clients share: the URL, plus the token header where the server takes one. */
function server(mcp: McpInfo): Record<string, unknown> {
  return mcp.auth === 'token' ? { url: mcp.url, headers: { Authorization: `Bearer ${mcp.token}` } } : { url: mcp.url }
}

/** The prompt that puts a coding agent to work on `artifact` through the MCP tools. */
export function artifactPrompt(artifact: Artifact): string {
  return (
    `I'm working on an OpenArtifact ${artifact.type} called "${artifact.title}" (id ${artifact.id}), served at ` +
    `${artifact.url}. Use the openartifact MCP server to edit it: read the server's instructions and its skill ` +
    `(the resource skill://openartifact/SKILL.md) first. The artifact's files are mounted at /artifact for ` +
    `run_code: main.md is the content, artifact.toml the config, styles.css the stylesheet. Edit them with ` +
    `run_code, call build after every edit, and look at the result with screenshot. Edit this artifact, do not ` +
    `create a new one.\n\n` +
    `Start by reading the files and telling me in a few lines what the ${artifact.type} contains and how it is ` +
    `styled, then ask me what to change.`
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(timer)
  }, [copied])
  return (
    <Button
      variant="primary"
      className="h-7 px-2.5"
      onClick={() => {
        navigator.clipboard.writeText(text).then(
          () => setCopied(true),
          () => undefined,
        )
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {copied ? 'Copied' : 'Copy'}
    </Button>
  )
}

function Snippet({ text }: { text: string }) {
  return (
    <div className="relative">
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-bg p-3 pr-24 font-mono text-xs leading-relaxed">
        {text}
      </pre>
      <div className="absolute top-2 right-2">
        <CopyButton text={text} />
      </div>
    </div>
  )
}

function Setup({ mcp }: { mcp: McpInfo }) {
  const [clientId, setClientId] = useState(CLIENTS[0].id)
  const client = CLIENTS.find((c) => c.id === clientId) ?? CLIENTS[0]
  const snippet = client.snippet(mcp)
  return (
    <div className="grid gap-3">
      <p className="text-sm text-muted">
        The MCP endpoint is <code className="text-fg">{mcp.url}</code>
        {mcp.auth === 'oauth'
          ? '. Clients sign in with Google the first time; an agent then works as you, on your artifacts.'
          : '. This server takes its development token, which the snippets include.'}
      </p>
      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Client">
        {CLIENTS.map((c) => (
          <button
            key={c.id}
            type="button"
            role="tab"
            aria-selected={c.id === clientId}
            onClick={() => setClientId(c.id)}
            className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
              c.id === clientId ? 'border-accent bg-accent/15 text-fg' : 'border-line text-muted hover:text-fg'
            }`}
          >
            {c.name}
          </button>
        ))}
      </div>
      <p className="text-xs text-muted">{snippet.where}</p>
      <Snippet text={snippet.text} />
    </div>
  )
}

/**
 * The dialog itself: the Set up tab alone from the list, or, on an artifact, the Prompt tab first and Set up
 * beside it.
 */
export function McpDialog({
  open,
  onClose,
  artifact = null,
}: {
  open: boolean
  onClose: () => void
  artifact?: Artifact | null
}) {
  const [tab, setTab] = useState<Tab>(artifact ? 'prompt' : 'setup')
  const [mcp, setMcp] = useState<McpInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open || mcp) return
    api.configure().then(
      (configure) => setMcp(configure.mcp),
      (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
    )
  }, [open, mcp])
  const tabs: Array<[Tab, string]> = artifact
    ? [
        ['prompt', 'Prompt'],
        ['setup', 'Set up MCP'],
      ]
    : [['setup', 'Set up MCP']]
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={artifact ? 'Work on this artifact from your coding agent' : 'Connect over MCP'}
      wide
    >
      {tabs.length > 1 ? (
        <div className="mb-4 flex gap-1 border-b border-line" role="tablist">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`-mb-px border-b-2 px-3 py-1.5 text-sm font-medium transition ${
                tab === id ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}
      {tab === 'prompt' && artifact ? (
        <div className="grid gap-3">
          <p className="text-sm text-muted">
            Paste this into Claude Code, Codex, Cursor or any agent connected to this server (see Set up MCP) and it
            will pick the artifact up and edit it through the same tools the chat here uses.
          </p>
          <Snippet text={artifactPrompt(artifact)} />
        </div>
      ) : error ? (
        <p className="text-sm text-danger">{error}</p>
      ) : mcp ? (
        <Setup mcp={mcp} />
      ) : (
        <Spinner />
      )}
      <div className="mt-5 flex justify-end">
        <Button onClick={onClose}>Close</Button>
      </div>
    </Dialog>
  )
}
