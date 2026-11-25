import { OrthographicView, type PickingInfo } from '@deck.gl/core'
import { LineLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import DeckGL, { type DeckGLRef } from '@deck.gl/react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Lasso, MessageSquare, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { usePaperPanel } from '@/components/paper-panel'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { api, ApiError, type MapPoint } from '@/lib/api'
import { clusterColors, rqColors, sequential, toCss, type Legend, type RGB } from '@/lib/colors'
import { bounds, pointInPolygon, type Pt } from '@/lib/geometry'
import { mapRoute } from '@/router'

type ColorBy = 'cluster' | 'rq' | 'year'
const FADED = 38

export function MapPage() {
  const { project } = mapRoute.useParams()
  const navigate = useNavigate()
  const panel = usePaperPanel()
  const [method, setMethod] = useState<'umap' | 'tsne'>('umap')
  const [colorBy, setColorBy] = useState<ColorBy>('cluster')
  const [edges, setEdges] = useState(false)
  const [labels, setLabels] = useState(true)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [lassoMode, setLassoMode] = useState(false)
  const [lasso, setLasso] = useState<Pt[] | null>(null)
  const [hover, setHover] = useState<{ x: number; y: number; p: MapPoint } | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  type VS = { target: [number, number, number]; zoom: number }
  const [viewState, setViewState] = useState<{ key: string; vs: VS } | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const deck = useRef<DeckGLRef>(null)

  const { data, error, isLoading } = useQuery({
    queryKey: ['map', project, method], queryFn: () => api.map(project, method), placeholderData: (prev) => prev,
  })
  const { data: cites } = useQuery({ queryKey: ['citations', project], queryFn: () => api.citations(project), enabled: edges })
  const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const rqList = projects?.find((p) => p.slug === project)?.rqs
  const rqIds = useMemo(() => rqList?.map((r) => r.id) ?? [], [rqList])

  useEffect(() => {
    if (!box.current) return
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }))
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [])

  // Fit the view whenever the layout (method) or the canvas size changes.
  const points = useMemo(() => data?.points ?? [], [data])
  const clusters = useMemo(() => data?.clusters ?? [], [data])
  // Fit-to-data view, recomputed when the layout (method) or canvas size changes; user pan/zoom overrides it
  // until then (keyed by `fitKey`).
  const fitKey = `${data?.method}:${size.w}x${size.h}:${points.length}`
  const fitted = useMemo(() => {
    if (!points.length || !size.w) return null
    const b = bounds(points.map((p) => [p.x, p.y]), 0.08)
    const zoom = Math.log2(Math.min(size.w / (b.maxX - b.minX), size.h / (b.maxY - b.minY)))
    return { target: [(b.minX + b.maxX) / 2, (b.minY + b.maxY) / 2, 0] as [number, number, number], zoom }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey])
  const view = viewState && viewState.key === fitKey ? viewState.vs : fitted

  const byId = useMemo(() => new Map(points.map((p) => [p.id, p])), [points])

  const coloring = useMemo((): { color: (p: MapPoint) => RGB; legend: Legend[] } => {
    if (colorBy === 'rq') {
      const c = rqColors(rqIds)
      return { color: (p) => c.color(p.rq_ids), legend: c.legend }
    }
    if (colorBy === 'year') {
      const ys = points.map((p) => p.year).filter((y): y is number => y !== null)
      const lo = Math.min(...ys), hi = Math.max(...ys)
      const t = (y: number | null) => (y === null || hi === lo ? 0.5 : (y - lo) / (hi - lo))
      return {
        color: (p) => sequential(t(p.year)),
        legend: ys.length ? [{ label: String(lo), color: sequential(0) }, { label: String(hi), color: sequential(1) }] : [],
      }
    }
    const c = clusterColors(clusters)
    return { color: (p) => c.color(p.cluster_id), legend: c.legend }
  }, [colorBy, points, clusters, rqIds])

  const q = search.trim().toLowerCase()
  const matches = useMemo(() => (q ? new Set(points.filter((p) => p.title.toLowerCase().includes(q)).map((p) => p.id)) : null),
    [q, points])
  const focus = selected.size ? selected : matches
  const isFocused = (id: string) => !focus || focus.has(id)

  const edgeData = useMemo(() => (edges && cites ? cites.filter((e) => byId.has(e.source) && byId.has(e.target)) : []),
    [edges, cites, byId])

  const layers = [
    edges && new LineLayer({
      id: 'edges', data: edgeData,
      getSourcePosition: (e) => [byId.get(e.source)!.x, byId.get(e.source)!.y],
      getTargetPosition: (e) => [byId.get(e.target)!.x, byId.get(e.target)!.y],
      getColor: (e) => (hover && (e.source === hover.p.id || e.target === hover.p.id) ? [40, 40, 40, 200]
        : [120, 120, 120, focus && !(focus.has(e.source) || focus.has(e.target)) ? 12 : 50]),
      getWidth: 1, widthUnits: 'pixels',
      updateTriggers: { getColor: [hover?.p.id, focus] },
    }),
    new ScatterplotLayer<MapPoint>({
      id: 'papers', data: points, pickable: true,
      getPosition: (p) => [p.x, p.y],
      getRadius: (p) => 4 + Math.min(6, Math.log1p(p.cited_by_count ?? 0) * 0.7),
      radiusUnits: 'pixels',
      getFillColor: (p) => [...coloring.color(p), isFocused(p.id) ? 230 : FADED],
      stroked: true, getLineColor: (p) => (selected.has(p.id) ? [20, 20, 20, 255] : [255, 255, 255, isFocused(p.id) ? 255 : 60]),
      getLineWidth: (p) => (selected.has(p.id) ? 2 : 1), lineWidthUnits: 'pixels',
      transitions: { getPosition: 600 },
      updateTriggers: { getFillColor: [coloring, focus], getLineColor: [selected, focus], getLineWidth: [selected] },
      onHover: (info: PickingInfo<MapPoint>) => setHover(info.object ? { x: info.x, y: info.y, p: info.object } : null),
      onClick: (info: PickingInfo<MapPoint>) => { if (info.object) panel.open(info.object.id) },
    }),
    labels && colorBy === 'cluster' && new TextLayer({
      id: 'labels', data: clusters.filter((c) => c.centroid && c.id >= 0),
      getPosition: (c) => c.centroid!, getText: (c) => c.label, getSize: 13,
      getColor: [11, 11, 11, 230], fontWeight: 600, background: true, getBackgroundColor: [255, 255, 255, 200],
      backgroundPadding: [4, 2], characterSet: 'auto', fontFamily: 'Geist Variable, system-ui, sans-serif',
    }),
  ]

  function finishLasso(poly: Pt[]) {
    const vp = deck.current?.deck?.getViewports()[0]
    setLasso(null)
    if (!vp || poly.length < 3) return
    const world = poly.map((pt) => vp.unproject(pt).slice(0, 2) as Pt)
    setSelected(new Set(points.filter((p) => pointInPolygon([p.x, p.y], world)).map((p) => p.id)))
    setLassoMode(false)
  }

  const selectedPapers = [...selected].map((id) => byId.get(id)).filter(Boolean) as MapPoint[]
  const notReady = error instanceof ApiError && error.status === 404

  return (
    <div className="flex h-full">
      <div ref={box} className="relative min-w-0 flex-1 bg-[#fcfcfb]">
        {notReady && <Centered>No map yet. Run <code>uv run lit map -p {project}</code>.</Centered>}
        {error && !notReady && <Centered className="text-destructive">{error.message}</Centered>}
        {isLoading && <Centered>Loading map…</Centered>}
        {view && (
          <DeckGL ref={deck} views={new OrthographicView({ id: 'ortho' })} layers={layers}
                  viewState={view} onViewStateChange={({ viewState: v }) => setViewState({ key: fitKey, vs: v as VS })}
                  controller={{ dragPan: !lassoMode, doubleClickZoom: !lassoMode }}
                  getCursor={({ isHovering }) => (lassoMode ? 'crosshair' : isHovering ? 'pointer' : 'grab')} />
        )}
        {lassoMode && (
          <svg className="absolute inset-0 size-full cursor-crosshair touch-none"
               onPointerDown={(e) => { (e.target as Element).setPointerCapture(e.pointerId); setLasso([[e.nativeEvent.offsetX, e.nativeEvent.offsetY]]) }}
               onPointerMove={(e) => lasso && setLasso([...lasso, [e.nativeEvent.offsetX, e.nativeEvent.offsetY]])}
               onPointerUp={() => lasso && finishLasso(lasso)}>
            {lasso && <polygon points={lasso.map((p) => p.join(',')).join(' ')}
                               className="fill-primary/10 stroke-primary" strokeWidth={1.5} strokeDasharray="4 3" />}
          </svg>
        )}
        {hover && !lassoMode && (
          <div className="pointer-events-none absolute z-10 max-w-xs rounded-md border bg-popover px-3 py-2 text-xs shadow-md"
               style={{ left: Math.min(hover.x + 12, size.w - 300), top: hover.y + 12 }}>
            <p className="font-medium text-foreground">{hover.p.title}</p>
            <p className="text-muted-foreground">
              {hover.p.year ?? 'n.d.'} · {hover.p.cited_by_count ?? 0} citations · {hover.p.rq_ids.join(', ') || 'no RQ'}
              {' · '}{clusters.find((c) => c.id === hover.p.cluster_id)?.label ?? 'Unclustered'}
            </p>
          </div>
        )}
        {points.length > 0 && (
          <div className="absolute bottom-3 left-3 max-w-[60%] rounded-md border bg-background/90 p-2 text-xs">
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {coloring.legend.map((l) => (
                <span key={l.label} className="flex items-center gap-1.5">
                  <span className="size-2.5 rounded-full" style={{ background: toCss(l.color) }} />
                  <span className="max-w-48 truncate">{l.label}</span>
                </span>
              ))}
            </div>
            <p className="mt-1 text-muted-foreground">Point size: citation count</p>
          </div>
        )}
      </div>

      <aside className="w-80 shrink-0 space-y-5 overflow-y-auto border-l p-4 text-sm">
        <Field label="Layout">
          <ToggleGroup type="single" variant="outline" size="sm" value={method}
                       onValueChange={(v) => v && setMethod(v as typeof method)}>
            <ToggleGroupItem value="umap">UMAP</ToggleGroupItem>
            <ToggleGroupItem value="tsne">t-SNE</ToggleGroupItem>
          </ToggleGroup>
        </Field>
        <Field label="Colour by">
          <ToggleGroup type="single" variant="outline" size="sm" value={colorBy}
                       onValueChange={(v) => v && setColorBy(v as ColorBy)}>
            <ToggleGroupItem value="cluster">Cluster</ToggleGroupItem>
            <ToggleGroupItem value="rq">RQ</ToggleGroupItem>
            <ToggleGroupItem value="year">Year</ToggleGroupItem>
          </ToggleGroup>
        </Field>
        <div className="space-y-2">
          <Label className="flex items-center gap-2 font-normal">
            <Checkbox checked={labels} onCheckedChange={(v) => setLabels(v === true)} />Cluster labels
          </Label>
          <Label className="flex items-center gap-2 font-normal">
            <Checkbox checked={edges} onCheckedChange={(v) => setEdges(v === true)} />
            Citation links{edges && cites ? ` (${edgeData.length})` : ''}
          </Label>
        </div>
        <Field label="Highlight titles">
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="e.g. spindle" />
          {matches && <p className="text-xs text-muted-foreground">{matches.size} matching papers
            {matches.size > 0 && <> · <button className="underline" onClick={() => setSelected(new Set(matches))}>select them</button></>}</p>}
        </Field>
        <Field label="Selection">
          <div className="flex gap-2">
            <Button size="sm" variant={lassoMode ? 'default' : 'outline'} onClick={() => setLassoMode(!lassoMode)}>
              <Lasso />{lassoMode ? 'Drawing… (click to cancel)' : 'Lasso'}
            </Button>
            {selected.size > 0 && <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}><X />Clear</Button>}
          </div>
          {selected.size > 0 && (
            <div className="space-y-2 pt-1">
              <Button size="sm" className="w-full"
                      onClick={() => navigate({ to: '/p/$project/ask', params: { project }, search: { papers: [...selected].join(',') } })}>
                <MessageSquare />Ask about {selected.size} papers
              </Button>
              <ul className="max-h-60 space-y-1 overflow-y-auto">
                {selectedPapers.map((p) => (
                  <li key={p.id}><button className="text-left hover:underline" onClick={() => panel.open(p.id)}>
                    {p.title} <span className="text-muted-foreground">({p.year ?? 'n.d.'})</span></button></li>
                ))}
              </ul>
            </div>
          )}
        </Field>
        {clusters.length > 0 && (
          <Field label={`Clusters · ${clusters.length}`}>
            <ul className="space-y-2">
              {clusters.map((c) => (
                <li key={c.id}>
                  <button className="text-left" title="Select this cluster's papers"
                          onClick={() => setSelected(new Set(points.filter((p) => p.cluster_id === c.id).map((p) => p.id)))}>
                    <span className="font-medium hover:underline">{c.label}</span>
                    <span className="text-muted-foreground"> · {c.size}</span>
                  </button>
                  {c.description && <p className="text-xs text-muted-foreground">{c.description}</p>}
                </li>
              ))}
            </ul>
          </Field>
        )}
      </aside>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1.5"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>{children}</div>
}

function Centered({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={`absolute inset-0 flex items-center justify-center text-muted-foreground ${className ?? ''}`}>{children}</div>
}
