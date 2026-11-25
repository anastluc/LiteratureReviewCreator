import { useQuery } from '@tanstack/react-query'
import {
  createRootRoute, createRoute, createRouter, lazyRouteComponent, Link, Outlet, redirect, useParams,
} from '@tanstack/react-router'
import { BookOpen } from 'lucide-react'

import { PaperPanelProvider } from '@/components/paper-panel'
import { api } from '@/lib/api'
import { AskPage } from '@/pages/ask'
import { PapersPage } from '@/pages/papers'
import { ProjectsPage } from '@/pages/projects'
import { ReportsPage } from '@/pages/reports'

const rootRoute = createRootRoute({ component: () => <Outlet /> })

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: ProjectsPage })

const projectRoute = createRoute({ getParentRoute: () => rootRoute, path: '/p/$project', component: ProjectLayout })

const projectIndex = createRoute({
  getParentRoute: () => projectRoute, path: '/',
  beforeLoad: ({ params }) => { throw redirect({ to: '/p/$project/ask', params }) },
})

export interface AskSearch { papers?: string; q?: string }
export const askRoute = createRoute({
  getParentRoute: () => projectRoute, path: 'ask', component: AskPage,
  validateSearch: (s: Record<string, unknown>): AskSearch => ({
    papers: typeof s.papers === 'string' && s.papers ? s.papers : undefined,
    q: typeof s.q === 'string' && s.q ? s.q : undefined,
  }),
})

export const mapRoute = createRoute({ getParentRoute: () => projectRoute, path: 'map',
  component: lazyRouteComponent(() => import('@/pages/map'), 'MapPage') })
export const papersRoute = createRoute({ getParentRoute: () => projectRoute, path: 'papers', component: PapersPage })

export interface ReportsSearch { name?: string }
export const reportsRoute = createRoute({
  getParentRoute: () => projectRoute, path: 'reports', component: ReportsPage,
  validateSearch: (s: Record<string, unknown>): ReportsSearch => ({
    name: typeof s.name === 'string' && s.name ? s.name : undefined,
  }),
})

const routeTree = rootRoute.addChildren([
  indexRoute, projectRoute.addChildren([projectIndex, askRoute, mapRoute, papersRoute, reportsRoute]),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register { router: typeof router }
}

const NAV = [
  { to: '/p/$project/ask', label: 'Ask' },
  { to: '/p/$project/map', label: 'Map' },
  { to: '/p/$project/papers', label: 'Papers' },
  { to: '/p/$project/reports', label: 'Reports' },
] as const

function ProjectLayout() {
  const { project } = useParams({ from: '/p/$project' })
  const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const info = projects?.find((p) => p.slug === project)
  return (
    <PaperPanelProvider project={project}>
      <div className="flex h-dvh flex-col">
        <header className="flex shrink-0 items-center gap-6 border-b px-4 py-2">
          <Link to="/" className="flex items-center gap-2 font-semibold"><BookOpen className="size-5" />litkb</Link>
          <span className="text-sm text-muted-foreground">
            {project}{info ? ` · ${info.papers} papers` : ''}
          </span>
          <nav className="flex gap-1">
            {NAV.map((n) => (
              <Link key={n.to} to={n.to} params={{ project }}
                    className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
                    activeProps={{ className: 'bg-accent text-foreground font-medium' }}>
                {n.label}
              </Link>
            ))}
          </nav>
        </header>
        <main className="min-h-0 flex-1"><Outlet /></main>
      </div>
    </PaperPanelProvider>
  )
}
