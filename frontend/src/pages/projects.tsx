import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { BookOpen } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'

export function ProjectsPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-8">
      <h1 className="flex items-center gap-2 text-2xl font-semibold"><BookOpen />litkb projects</h1>
      {isLoading && <Skeleton className="h-24" />}
      {error && <p className="text-destructive">Can't reach the API: {error.message}. Is `uv run lit serve` running?</p>}
      {data?.length === 0 && (
        <p className="text-muted-foreground">No projects yet. Create one with <code>uv run lit new &lt;slug&gt;</code>.</p>
      )}
      <div className="grid gap-4">
        {data?.map((p) => (
          <Link key={p.slug} to="/p/$project/ask" params={{ project: p.slug }}>
            <Card className="transition-colors hover:bg-accent/50">
              <CardHeader>
                <CardTitle>{p.slug}</CardTitle>
                <CardDescription className="space-y-2">
                  <span>{p.papers} included papers · {p.rqs?.length ?? 0} research questions
                    {p.has_map ? ' · map ready' : ''}{p.reports.length ? ` · ${p.reports.length} reports` : ''}</span>
                  {p.rqs && p.rqs.length > 0 && (
                    <span className="flex flex-col gap-1 pt-1">
                      {p.rqs.map((r) => (
                        <span key={r.id} className="flex gap-2"><Badge variant="secondary">{r.id}</Badge>
                          <span className="line-clamp-1">{r.text}</span></span>
                      ))}
                    </span>
                  )}
                </CardDescription>
              </CardHeader>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  )
}
