import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Loader2, Send, Square, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Markdown } from '@/components/markdown'
import { usePaperPanel } from '@/components/paper-panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { api, streamQuery, type QueryFilters, type Source } from '@/lib/api'
import { markInvalid } from '@/lib/citations'
import { cn } from '@/lib/utils'
import { askRoute } from '@/router'

interface Turn {
  question: string
  filters: QueryFilters
  sources: Source[]
  answer: string
  status: 'streaming' | 'done' | 'error'
  error?: string
  invalid?: string[]
}

const storeKey = (p: string) => `litkb:ask:${p}`

export function AskPage() {
  const { project } = askRoute.useParams()
  const search = askRoute.useSearch()
  const navigate = useNavigate({ from: askRoute.fullPath })
  const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const rqs = projects?.find((p) => p.slug === project)?.rqs ?? []

  const [turns, setTurns] = useState<Turn[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(storeKey(project)) ?? '[]') } catch { return [] }
  })
  const [question, setQuestion] = useState(search.q ?? '')
  const [rqFilter, setRqFilter] = useState<string[]>([])
  const [yearMin, setYearMin] = useState('')
  const [yearMax, setYearMax] = useState('')
  const abort = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const selection = search.papers?.split(',').filter(Boolean) ?? []
  const busy = turns.at(-1)?.status === 'streaming'

  useEffect(() => {
    if (!busy) sessionStorage.setItem(storeKey(project), JSON.stringify(turns))
  }, [turns, busy, project])
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }) }, [turns])

  const update = (fn: (t: Turn) => Turn) => setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])])

  async function ask() {
    const q = question.trim()
    if (!q || busy) return
    const filters: QueryFilters = {
      paper_ids: selection.length ? selection : undefined,
      rq_ids: rqFilter.length ? rqFilter : undefined,
      year_min: yearMin ? Number(yearMin) : undefined,
      year_max: yearMax ? Number(yearMax) : undefined,
    }
    setTurns((ts) => [...ts, { question: q, filters, sources: [], answer: '', status: 'streaming' }])
    setQuestion('')
    abort.current = new AbortController()
    try {
      await streamQuery(project, q, filters, {
        onSources: (sources) => update((t) => ({ ...t, sources })),
        onToken: (tok) => update((t) => ({ ...t, answer: t.answer + tok })),
        onDone: (d) => update((t) => ({ ...t, status: 'done', invalid: d.invalid_citations })),
        onError: (error) => update((t) => ({ ...t, status: 'error', error })),
      }, abort.current.signal)
      update((t) => (t.status === 'streaming' ? { ...t, status: 'done' } : t))
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === 'AbortError'
      update((t) => ({ ...t, status: aborted ? 'done' : 'error', error: aborted ? undefined : String(e) }))
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-8 p-6">
          {turns.length === 0 && (
            <div className="space-y-2 pt-12 text-center text-muted-foreground">
              <p className="text-lg text-foreground">Ask the knowledge base</p>
              <p className="text-sm">Answers cite papers as [W…]; click a citation to open the paper.
                Select papers on the Map to ask about just those.</p>
            </div>
          )}
          {turns.map((t, i) => <TurnView key={i} turn={t} rqs={rqs.map((r) => r.id)} />)}
          <div ref={bottom} />
        </div>
      </div>
      <div className="shrink-0 border-t bg-background">
        <div className="mx-auto max-w-3xl space-y-2 p-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {selection.length > 0 && (
              <Badge className="gap-1">
                {selection.length} selected papers
                <button aria-label="Clear selection" onClick={() => navigate({ search: { papers: undefined } })}>
                  <X className="size-3" />
                </button>
              </Badge>
            )}
            {rqs.map((r) => (
              <button key={r.id} title={r.text}
                      onClick={() => setRqFilter((f) => (f.includes(r.id) ? f.filter((x) => x !== r.id) : [...f, r.id]))}
                      className={cn('rounded-md border px-2 py-0.5 text-xs',
                        rqFilter.includes(r.id) ? 'border-primary bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-accent')}>
                {r.id}
              </button>
            ))}
            <span className="ml-auto flex items-center gap-1 text-muted-foreground">
              Years
              <Input className="h-7 w-20" inputMode="numeric" placeholder="from" value={yearMin}
                     onChange={(e) => setYearMin(e.target.value.replace(/\D/g, ''))} />
              –
              <Input className="h-7 w-20" inputMode="numeric" placeholder="to" value={yearMax}
                     onChange={(e) => setYearMax(e.target.value.replace(/\D/g, ''))} />
            </span>
            {turns.length > 0 && !busy && (
              <Button size="icon-sm" variant="ghost" title="Clear conversation" onClick={() => setTurns([])}>
                <Trash2 />
              </Button>
            )}
          </div>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask() }}>
            <Textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={2}
                      placeholder="Ask a question about the literature…" className="min-h-0 resize-none"
                      onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }} />
            {busy
              ? <Button type="button" variant="outline" onClick={() => abort.current?.abort()}><Square />Stop</Button>
              : <Button type="submit" disabled={!question.trim()}><Send />Ask</Button>}
          </form>
        </div>
      </div>
    </div>
  )
}

function TurnView({ turn, rqs }: { turn: Turn; rqs: string[] }) {
  const titles = new Map(turn.sources.map((s) => [s.paper_id, `${s.citation ?? ''} — ${s.title ?? ''}`]))
  const f = turn.filters
  const scope = [
    f.paper_ids && `${f.paper_ids.length} selected papers`,
    f.rq_ids && f.rq_ids.length < rqs.length && f.rq_ids.join(', '),
    (f.year_min || f.year_max) && `${f.year_min ?? '…'}–${f.year_max ?? '…'}`,
  ].filter(Boolean)
  return (
    <div className="space-y-3">
      <div className="ml-auto w-fit max-w-[85%] rounded-lg bg-muted px-4 py-2">
        <p className="whitespace-pre-wrap">{turn.question}</p>
        {scope.length > 0 && <p className="text-xs text-muted-foreground">Scope: {scope.join(' · ')}</p>}
      </div>
      {turn.answer
        ? <Markdown source={markInvalid(turn.answer, turn.invalid ?? [])} titles={titles} />
        : turn.status === 'streaming' && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />{turn.sources.length ? 'Writing…' : 'Searching…'}</p>
        )}
      {turn.status === 'error' && <p className="text-sm text-destructive">{turn.error}</p>}
      {turn.invalid && turn.invalid.length > 0 && (
        <p className="text-xs text-amber-700">Unverified citations (not in the retrieved sources): {turn.invalid.join(', ')}</p>
      )}
      {turn.sources.length > 0 && <Sources sources={turn.sources} />}
    </div>
  )
}

function Sources({ sources }: { sources: Source[] }) {
  const panel = usePaperPanel()
  const [open, setOpen] = useState(false)
  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)} className="text-sm">
      <summary className="cursor-pointer text-muted-foreground">{sources.length} retrieved excerpts</summary>
      <ol className="mt-2 space-y-2">
        {sources.map((s) => {
          const isReport = s.kind === 'undermind_report'
          return (
            <li key={s.chunk_id} className="rounded-md border p-3">
              <div className="mb-1 flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
                <span className="font-mono">[{s.n}]</span>
                {isReport
                  ? <span>Undermind summary ({s.paper_id.split(':')[1]}) · not citable</span>
                  : <button className="text-left font-medium text-foreground hover:underline"
                            onClick={() => panel.open(s.paper_id, s.page)}>
                      {s.citation} — {s.title}
                    </button>}
                <span>{[s.section || s.kind, s.page && `p. ${s.page}`].filter(Boolean).join(', ')}</span>
                {!isReport && <span className="font-mono">{s.paper_id}</span>}
              </div>
              <p className="line-clamp-4 text-muted-foreground">{s.text}</p>
            </li>
          )
        })}
      </ol>
    </details>
  )
}
