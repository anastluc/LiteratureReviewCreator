import { useQuery } from '@tanstack/react-query'
import { ExternalLink, FileText } from 'lucide-react'
import { createContext, useContext, useState, type ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { api, ApiError, pdfUrl } from '@/lib/api'

interface PanelState { open: (id: string, page?: number | null) => void }
const Ctx = createContext<PanelState>({ open: () => {} })

export const usePaperPanel = () => useContext(Ctx)

/** Side panel showing one paper; any component can call `usePaperPanel().open(id)`. */
export function PaperPanelProvider({ project, children }: { project: string; children: ReactNode }) {
  const [current, setCurrent] = useState<{ id: string; page?: number | null } | null>(null)
  return (
    <Ctx.Provider value={{ open: (id, page) => setCurrent({ id, page }) }}>
      {children}
      <Sheet open={current !== null} onOpenChange={(o) => !o && setCurrent(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          {current && <PaperDetails project={project} id={current.id} page={current.page}
                                    onOpen={(id) => setCurrent({ id })} />}
        </SheetContent>
      </Sheet>
    </Ctx.Provider>
  )
}

function PaperDetails({ project, id, page, onOpen }: {
  project: string; id: string; page?: number | null; onOpen: (id: string) => void
}) {
  const { data: p, error, isLoading } = useQuery({ queryKey: ['paper', project, id], queryFn: () => api.paper(project, id) })
  // Cluster labels come with the map (shared cache with the Map page); absent until `lit map` has run.
  const { data: map } = useQuery({
    queryKey: ['map', project, 'umap'], queryFn: () => api.map(project, 'umap'),
    retry: (_n, e) => !(e instanceof ApiError && e.status === 404) && _n < 2,
  })
  const clusterLabel = p?.cluster_id != null && p.cluster_id >= 0
    ? map?.clusters.find((c) => c.id === p.cluster_id)?.label ?? `Cluster ${p.cluster_id}` : null
  if (isLoading) return <div className="space-y-3 p-6"><Skeleton className="h-6 w-3/4" /><Skeleton className="h-32" /></div>
  if (error || !p) {
    return (
      <SheetHeader>
        <SheetTitle>{id}</SheetTitle>
        <SheetDescription>{error ? String(error.message) : 'Paper not found'}</SheetDescription>
      </SheetHeader>
    )
  }
  const pdf = pdfUrl(project, p.id) + (page ? `#page=${page}` : '')
  return (
    <>
      <SheetHeader>
        <SheetTitle className="pr-6 text-lg leading-snug">{p.title}</SheetTitle>
        <SheetDescription>
          {p.authors.slice(0, 8).join(', ')}{p.authors.length > 8 ? ', et al.' : ''}
          {' · '}{p.year ?? 'n.d.'}{p.venue ? ` · ${p.venue}` : ''}
        </SheetDescription>
      </SheetHeader>
      <div className="space-y-4 px-4 pb-6 text-sm">
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="outline" className="font-mono">{p.id}</Badge>
          {p.rq_ids.map((r) => <Badge key={r} variant="secondary">{r}</Badge>)}
          {clusterLabel && <Badge variant="secondary" className="max-w-full truncate">{clusterLabel}</Badge>}
          {p.cited_by_count !== null && <Badge variant="outline">{p.cited_by_count} citations</Badge>}
          {!p.included && <Badge variant="destructive">Excluded</Badge>}
        </div>
        <div className="flex flex-wrap gap-2">
          {p.has_pdf
            ? <Button asChild size="sm"><a href={pdf} target="_blank" rel="noreferrer"><FileText />
                PDF{page ? ` (p. ${page})` : ''}</a></Button>
            : <Button size="sm" disabled><FileText />No PDF ({p.pdf_status ?? 'pending'})</Button>}
          {p.doi && <Button asChild size="sm" variant="outline">
            <a href={`https://doi.org/${p.doi}`} target="_blank" rel="noreferrer"><ExternalLink />DOI</a></Button>}
          {p.id.startsWith('W') && <Button asChild size="sm" variant="outline">
            <a href={`https://openalex.org/${p.id}`} target="_blank" rel="noreferrer"><ExternalLink />OpenAlex</a></Button>}
        </div>
        <Separator />
        <section>
          <h3 className="mb-1 font-medium">Abstract</h3>
          <p className="leading-relaxed text-muted-foreground">{p.abstract || 'No abstract available.'}</p>
        </section>
        {p.undermind.length > 0 && (
          <section>
            <h3 className="mb-1 font-medium">Found by Undermind</h3>
            <ul className="text-muted-foreground">
              {p.undermind.map((u) => (
                <li key={u.rq_id}>
                  {u.rq_id}: rank {u.undermind_rank ?? '–'}
                  {u.undermind_score !== null ? `, relevance ${Math.round(u.undermind_score * 100)}%` : ''}
                </li>
              ))}
            </ul>
          </section>
        )}
        <CitationList project={project} title="Cites (in this corpus)" ids={p.cites} onOpen={onOpen} />
        <CitationList project={project} title="Cited by (in this corpus)" ids={p.cited_by} onOpen={onOpen} />
        <section className="text-xs text-muted-foreground">
          PDF: {p.pdf_status ?? 'pending'}{p.pdf_source ? ` via ${p.pdf_source}` : ''} · parsed: {p.parse_status ?? 'pending'}
          {' '}· indexed: {p.index_status ?? 'pending'}
        </section>
      </div>
    </>
  )
}

function CitationList({ project, title, ids, onOpen }: {
  project: string; title: string; ids: string[]; onOpen: (id: string) => void
}) {
  const { data: papers } = useQuery({ queryKey: ['papers', project], queryFn: () => api.papers(project), enabled: ids.length > 0 })
  if (!ids.length) return null
  const byId = new Map((papers ?? []).map((p) => [p.id, p]))
  return (
    <section>
      <h3 className="mb-1 font-medium">{title} · {ids.length}</h3>
      <ul className="space-y-1">
        {ids.map((id) => (
          <li key={id}>
            <button className="text-left text-primary underline-offset-2 hover:underline" onClick={() => onOpen(id)}>
              {byId.get(id)?.title ?? id}
            </button>
            {byId.get(id)?.year && <span className="text-muted-foreground"> ({byId.get(id)!.year})</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
