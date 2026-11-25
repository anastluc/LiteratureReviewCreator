// Map colours. Categorical slots in fixed order (validated default palette, light mode); a 9th+ category folds
// into "Other" rather than generating new hues. Year uses a single-hue (blue) sequential ramp.
export type RGB = [number, number, number]

const hex = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB

export const CATEGORICAL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
  .map(hex)
export const OTHER: RGB = hex('#8a8980')
export const UNCLUSTERED: RGB = hex('#c3c2b7')
export const toCss = ([r, g, b]: RGB) => `rgb(${r} ${g} ${b})`

const SEQ = ['#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281']
  .map(hex)

export function sequential(t: number): RGB {
  const x = Math.min(1, Math.max(0, t)) * (SEQ.length - 1)
  const i = Math.floor(x), f = x - i
  const a = SEQ[i], b = SEQ[Math.min(i + 1, SEQ.length - 1)]
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f)) as RGB
}

export interface Legend { label: string; color: RGB }

/** Colour per cluster id: the 8 largest clusters get the categorical slots (by id order, so a cluster keeps its
 *  colour when others are filtered), the rest are "Other", HDBSCAN noise (-1) is "Unclustered". */
export function clusterColors(clusters: { id: number; label: string; size: number }[]): {
  color: (id: number) => RGB
  legend: Legend[]
} {
  const top = [...clusters].sort((a, b) => b.size - a.size).slice(0, CATEGORICAL.length)
    .sort((a, b) => a.id - b.id)
  const m = new Map(top.map((c, i) => [c.id, CATEGORICAL[i]]))
  const legend: Legend[] = top.map((c) => ({ label: c.label, color: m.get(c.id)! }))
  if (clusters.length > top.length) legend.push({ label: 'Other clusters', color: OTHER })
  legend.push({ label: 'Unclustered', color: UNCLUSTERED })
  return { color: (id) => (id < 0 ? UNCLUSTERED : m.get(id) ?? OTHER), legend }
}

/** Colour by research question: a paper found by several RQs is coloured by "Several RQs". */
export function rqColors(rqIds: string[]): { color: (ids: string[]) => RGB; legend: Legend[] } {
  const shown = rqIds.slice(0, CATEGORICAL.length - 1)
  const m = new Map(shown.map((id, i) => [id, CATEGORICAL[i]]))
  const several = CATEGORICAL[CATEGORICAL.length - 1]
  const legend: Legend[] = shown.map((id) => ({ label: id, color: m.get(id)! }))
  if (rqIds.length > shown.length) legend.push({ label: 'Other RQs', color: OTHER })
  legend.push({ label: 'Several RQs', color: several })
  return {
    color: (ids) => (ids.length > 1 ? several : (ids.length ? m.get(ids[0]) ?? OTHER : UNCLUSTERED)),
    legend,
  }
}
