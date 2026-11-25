// Typed client for the litkb FastAPI backend (ARCHITECTURE.md §5.9). Same-origin: Vite proxies /api in dev,
// and FastAPI serves the built app in production.
import { readSSE } from './sse'

export interface RQ { id: string; text: string }

export interface ProjectInfo {
  slug: string
  papers: number
  has_map: boolean
  reports: string[]
  rqs?: RQ[]
}

export interface Paper {
  id: string
  doi: string | null
  title: string
  year: number | null
  venue: string | null
  cited_by_count: number | null
  is_oa: number | null
  included: number
  pdf_status: string | null
  pdf_source: string | null
  parse_status: string | null
  index_status: string | null
  landing_url: string | null
  authors: string[]
  rq_ids: string[]
  cluster_id: number | null
  has_pdf: boolean
}

export interface PaperDetail extends Paper {
  abstract: string | null
  undermind: { rq_id: string; undermind_rank: number | null; undermind_score: number | null }[]
  cites: string[]
  cited_by: string[]
}

export interface MapPoint {
  id: string
  x: number
  y: number
  cluster_id: number
  title: string
  year: number | null
  rq_ids: string[]
  cited_by_count: number | null
}

export interface Cluster {
  id: number
  label: string
  description?: string
  size: number
  keywords?: string[]
  top_papers?: string[]
  centroid: [number, number] | null
}

export interface MapData { method: 'tsne' | 'umap'; points: MapPoint[]; clusters: Cluster[] }

export interface Source {
  n: number
  chunk_id: string
  paper_id: string
  kind: string
  section: string | null
  page: number | null
  text: string
  title: string | null
  citation: string | null
}

export interface QueryFilters {
  paper_ids?: string[]
  rq_ids?: string[]
  year_min?: number
  year_max?: number
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not JSON */ }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

const enc = encodeURIComponent

export const api = {
  projects: () => get<ProjectInfo[]>('/api/projects'),
  papers: (p: string, includeExcluded = false) =>
    get<Paper[]>(`/api/${enc(p)}/papers${includeExcluded ? '?include_excluded=true' : ''}`),
  paper: (p: string, id: string) => get<PaperDetail>(`/api/${enc(p)}/papers/${enc(id)}`),
  map: (p: string, method: 'tsne' | 'umap') => get<MapData>(`/api/${enc(p)}/map?method=${method}`),
  citations: (p: string) => get<{ source: string; target: string }[]>(`/api/${enc(p)}/citations`),
  reports: (p: string) => get<string[]>(`/api/${enc(p)}/reports`),
  report: (p: string, name: string) => get<{ name: string; markdown: string }>(`/api/${enc(p)}/reports/${enc(name)}`),
}

export const pdfUrl = (p: string, id: string) => `/api/${enc(p)}/pdf/${enc(id)}`

export interface StreamHandlers {
  onSources: (s: Source[]) => void
  onToken: (t: string) => void
  onDone: (d: { citations: string[]; invalid_citations: string[] }) => void
  onError: (message: string) => void
}

/** POST a question and stream the answer (SSE over fetch, since EventSource cannot POST). */
export async function streamQuery(p: string, question: string, filters: QueryFilters, h: StreamHandlers,
                                  signal?: AbortSignal): Promise<void> {
  const res = await fetch(`/api/${enc(p)}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ question, ...filters, stream: true }),
    signal,
  })
  if (!res.ok || !res.body) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not JSON */ }
    h.onError(`${res.status}: ${detail}`)
    return
  }
  for await (const ev of readSSE(res.body)) {
    const data = ev.data ? JSON.parse(ev.data) : null
    if (ev.event === 'sources') h.onSources(data)
    else if (ev.event === 'token') h.onToken(data)
    else if (ev.event === 'done') h.onDone(data)
    else if (ev.event === 'error') h.onError(data?.message ?? 'Unknown error')
  }
}
