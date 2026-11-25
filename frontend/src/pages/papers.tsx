import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, FileText } from 'lucide-react'
import { useMemo, useState } from 'react'

import { usePaperPanel } from '@/components/paper-panel'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, pdfUrl, type Paper } from '@/lib/api'
import { papersRoute } from '@/router'

type SortKey = 'title' | 'year' | 'cited_by_count' | 'pdf_status'
const ALL = '__all'

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  ok: 'secondary', paywalled: 'outline', not_found: 'outline', failed: 'destructive',
}

export function PapersPage() {
  const { project } = papersRoute.useParams()
  const panel = usePaperPanel()
  const [includeExcluded, setIncludeExcluded] = useState(false)
  const [text, setText] = useState('')
  const [rq, setRq] = useState(ALL)
  const [pdf, setPdf] = useState(ALL)
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'cited_by_count', desc: true })

  const { data, error, isLoading } = useQuery({
    queryKey: ['papers', project, includeExcluded], queryFn: () => api.papers(project, includeExcluded),
  })
  const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const rqs = projects?.find((p) => p.slug === project)?.rqs ?? []

  const rows = useMemo(() => {
    const t = text.trim().toLowerCase()
    const out = (data ?? []).filter((p) =>
      (!t || p.title.toLowerCase().includes(t) || p.authors.some((a) => a.toLowerCase().includes(t)) || p.id.toLowerCase() === t)
      && (rq === ALL || p.rq_ids.includes(rq))
      && (pdf === ALL || (p.pdf_status ?? 'pending') === pdf))
    const k = sort.key
    const val = (p: Paper) => (k === 'title' ? p.title.toLowerCase() : k === 'pdf_status' ? p.pdf_status ?? '' : p[k] ?? -1)
    return out.sort((a, b) => {
      const x = val(a), y = val(b)
      return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1)
    })
  }, [data, text, rq, pdf, sort])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const p of data ?? []) c[p.pdf_status ?? 'pending'] = (c[p.pdf_status ?? 'pending'] ?? 0) + 1
    return c
  }, [data])

  const head = (key: SortKey, label: string, className?: string) => (
    <TableHead className={className}>
      <button className="flex items-center gap-1" onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== 'title' }))}>
        {label}{sort.key === key && (sort.desc ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
      </button>
    </TableHead>
  )

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b p-3 text-sm">
        <Input className="h-8 w-72" placeholder="Search title, author or id…" value={text} onChange={(e) => setText(e.target.value)} />
        <Select value={rq} onValueChange={setRq}>
          <SelectTrigger size="sm" className="w-32"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All RQs</SelectItem>
            {rqs.map((r) => <SelectItem key={r.id} value={r.id}>{r.id}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={pdf} onValueChange={setPdf}>
          <SelectTrigger size="sm" className="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Any PDF status</SelectItem>
            {Object.entries(counts).map(([s, n]) => <SelectItem key={s} value={s}>PDF {s} ({n})</SelectItem>)}
          </SelectContent>
        </Select>
        <Label className="flex items-center gap-2 font-normal">
          <Checkbox checked={includeExcluded} onCheckedChange={(v) => setIncludeExcluded(v === true)} />Show excluded
        </Label>
        <span className="ml-auto text-muted-foreground">{rows.length} of {data?.length ?? 0} papers</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading && <p className="p-6 text-muted-foreground">Loading…</p>}
        {error && <p className="p-6 text-destructive">{error.message}</p>}
        {data && (
          <Table>
            <TableHeader className="sticky top-0 bg-background">
              <TableRow>
                {head('title', 'Title')}
                {head('year', 'Year', 'w-16')}
                <TableHead className="w-28">RQs</TableHead>
                {head('cited_by_count', 'Cited by', 'w-24')}
                {head('pdf_status', 'PDF', 'w-32')}
                <TableHead className="w-28">Parsed / indexed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((p) => (
                <TableRow key={p.id} className="cursor-pointer" onClick={() => panel.open(p.id)}>
                  <TableCell className="max-w-0 whitespace-normal">
                    <p className="font-medium">{p.title}{!p.included && <Badge variant="destructive" className="ml-2">excluded</Badge>}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {p.authors.slice(0, 4).join(', ')}{p.authors.length > 4 ? ' et al.' : ''}{p.venue ? ` · ${p.venue}` : ''}
                    </p>
                  </TableCell>
                  <TableCell>{p.year ?? '–'}</TableCell>
                  <TableCell><div className="flex flex-wrap gap-1">{p.rq_ids.map((r) => <Badge key={r} variant="secondary">{r}</Badge>)}</div></TableCell>
                  <TableCell className="tabular-nums">{p.cited_by_count ?? '–'}</TableCell>
                  <TableCell>
                    {p.has_pdf
                      ? <a href={pdfUrl(project, p.id)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
                           className="inline-flex items-center gap-1 text-primary hover:underline"><FileText className="size-3.5" />{p.pdf_source ?? 'PDF'}</a>
                      : <Badge variant={STATUS_VARIANT[p.pdf_status ?? ''] ?? 'outline'}>{p.pdf_status ?? 'pending'}</Badge>}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{p.parse_status ?? 'pending'} / {p.index_status ?? 'pending'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  )
}
