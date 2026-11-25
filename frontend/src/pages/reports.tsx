import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Download } from 'lucide-react'
import { useMemo } from 'react'

import { Markdown, slugify } from '@/components/markdown'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { reportsRoute } from '@/router'

const LABELS: Record<string, string> = {
  report_by_rq: 'Report by research question',
  report_thematic: 'Thematic appendix',
  missing_papers: 'Missing papers',
}

export function ReportsPage() {
  const { project } = reportsRoute.useParams()
  const { name } = reportsRoute.useSearch()
  const navigate = useNavigate({ from: reportsRoute.fullPath })
  const { data: names, error } = useQuery({ queryKey: ['reports', project], queryFn: () => api.reports(project) })
  const current = name ?? names?.[0]
  const { data: report, isLoading } = useQuery({
    queryKey: ['report', project, current], queryFn: () => api.report(project, current!), enabled: !!current,
  })
  const toc = useMemo(() => (report?.markdown ?? '').split('\n')
    .map((l) => /^(#{2,3})\s+(.+)$/.exec(l))
    .filter((m): m is RegExpExecArray => !!m)
    .map((m) => ({ level: m[1].length, text: m[2].replace(/[*_`]/g, ''), id: slugify(m[2].replace(/[*_`]/g, '')) })), [report])

  function download() {
    if (!report) return
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([report.markdown], { type: 'text/markdown' }))
    a.download = `${project}-${report.name}.md`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  if (error) return <p className="p-6 text-destructive">{error.message}</p>
  if (names && names.length === 0) {
    return <p className="p-6 text-muted-foreground">No reports yet. Run <code>uv run lit report -p {project}</code> or the
      write-report skill.</p>
  }
  return (
    <div className="flex h-full">
      <aside className="w-72 shrink-0 space-y-4 overflow-y-auto border-r p-4 text-sm">
        <ul className="space-y-1">
          {names?.map((n) => (
            <li key={n}>
              <button onClick={() => navigate({ search: { name: n } })}
                      className={cn('w-full rounded-md px-2 py-1 text-left hover:bg-accent', n === current && 'bg-accent font-medium')}>
                {LABELS[n] ?? n}
              </button>
            </li>
          ))}
        </ul>
        {toc.length > 0 && (
          <nav className="space-y-1 border-t pt-3">
            {toc.map((h, i) => (
              <a key={i} href={`#${h.id}`} className={cn('block text-muted-foreground hover:text-foreground', h.level === 3 && 'pl-3 text-xs')}
                 onClick={(e) => { e.preventDefault(); document.getElementById(h.id)?.scrollIntoView({ behavior: 'smooth' }) }}>
                {h.text}
              </a>
            ))}
          </nav>
        )}
      </aside>
      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl p-8">
          {report && <div className="mb-4 flex justify-end"><Button size="sm" variant="outline" onClick={download}><Download />Markdown</Button></div>}
          {isLoading && <p className="text-muted-foreground">Loading…</p>}
          {report && <Markdown source={report.markdown} />}
        </div>
      </div>
    </div>
  )
}
