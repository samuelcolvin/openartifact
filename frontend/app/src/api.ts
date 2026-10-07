/**
 * Typed access to the JSON API (`backend/api.py`). Every call carries the session cookie; a 401 means the session
 * is gone, so the browser is sent to sign in and back.
 */

export interface Viewer {
  name: string | null
  email: string | null
  picture: string | null
}

export interface Organization {
  domain: string
  name: string
}

export interface Me {
  viewer: Viewer
  organization: Organization | null
}

export type ArtifactType = 'deck' | 'document' | 'page'
export type Theme = 'light' | 'dark' | 'markdown-light' | 'markdown-dark'
export type Visibility = 'private' | 'org' | 'public'

export interface Artifact {
  id: string
  title: string
  type: ArtifactType
  visibility: Visibility
  org_editable: boolean
  forked_from: string | null
  created_at: string
  updated_at: string
  owner_email: string | null
  can_edit: boolean
  can_manage: boolean
  url: string
}

export interface ArtifactList {
  mine: Artifact[]
  shared: Artifact[]
}

export interface ModelChoice {
  id: string
  name: string
}

export interface Configure {
  models: ModelChoice[]
  default: string | null
}

export interface NewArtifact {
  title: string
  type: ArtifactType
  theme: Theme
  placement: 'personal' | 'org'
  public: boolean
  org_editable: boolean
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function loginUrl(): string {
  return `/login?next=${encodeURIComponent(window.location.pathname)}`
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers: {
      accept: 'application/json',
      ...(init.body ? { 'content-type': 'application/json' } : {}),
      ...init.headers,
    },
  })
  if (response.status === 401) {
    window.location.assign(loginUrl())
    throw new ApiError(401, 'sign in required')
  }
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
      else if (body.detail) detail = JSON.stringify(body.detail)
    } catch {}
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  me: () => request<Me>('/api/me'),
  configure: () => request<Configure>('/api/configure'),
  artifacts: () => request<ArtifactList>('/api/artifacts'),
  create: (body: NewArtifact) => request<Artifact>('/api/artifacts', { method: 'POST', body: JSON.stringify(body) }),
  setAccess: (id: string, body: { public: boolean; org_editable: boolean }) =>
    request<Artifact>(`/api/artifacts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  chat: (id: string) => request<{ messages: unknown[]; model: string | null }>(`/api/artifacts/${id}/chat`),
  clearChat: (id: string) => request<void>(`/api/artifacts/${id}/chat`, { method: 'DELETE' }),
}

/** The artifact `/edit/<id>` is about: the list is the only endpoint that returns rows, so look it up there. */
export async function findArtifact(id: string): Promise<Artifact | null> {
  const { mine, shared } = await api.artifacts()
  return [...mine, ...shared].find((a) => a.id === id) ?? null
}
