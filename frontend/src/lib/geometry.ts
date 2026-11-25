export type Pt = [number, number]

/** Ray-casting point-in-polygon test. */
export function pointInPolygon([x, y]: Pt, poly: Pt[]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i]
    const [xj, yj] = poly[j]
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

/** Bounds of a point set, padded by `pad` (fraction of the span). */
export function bounds(pts: Pt[], pad = 0.05): { minX: number; maxX: number; minY: number; maxY: number } {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (const [x, y] of pts) {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x)
    minY = Math.min(minY, y); maxY = Math.max(maxY, y)
  }
  if (!pts.length) return { minX: -1, maxX: 1, minY: -1, maxY: 1 }
  const dx = (maxX - minX || 1) * pad, dy = (maxY - minY || 1) * pad
  return { minX: minX - dx, maxX: maxX + dx, minY: minY - dy, maxY: maxY + dy }
}
