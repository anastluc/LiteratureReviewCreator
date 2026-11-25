import { describe, expect, it } from 'vitest'

import { citedIds, linkifyCitations, markInvalid } from '../citations'
import { clusterColors, OTHER, UNCLUSTERED } from '../colors'
import { bounds, pointInPolygon } from '../geometry'
import { parseSSE } from '../sse'

describe('parseSSE', () => {
  it('parses complete blocks and keeps the remainder', () => {
    const { events, rest } = parseSSE('event: sources\r\ndata: [1]\r\n\r\nevent: token\ndata: "a"\n\nevent: tok')
    expect(events).toEqual([{ event: 'sources', data: '[1]' }, { event: 'token', data: '"a"' }])
    expect(rest).toBe('event: tok')
  })
  it('ignores comments and joins multi-line data', () => {
    expect(parseSSE(': ping\n\ndata: a\ndata: b\n\n').events).toEqual([{ event: 'message', data: 'a\nb' }])
  })
})

describe('citations', () => {
  it('links valid ids and leaves invalid ones as text', () => {
    const out = linkifyCitations('Sleep helps [W123, ?h0123456789]; see [W9; W10]. Not [Smith 2020].')
    expect(out).toContain(String.raw`\[[W123](#cite-W123), ?h0123456789\]`)
    expect(out).toContain(String.raw`\[[W9](#cite-W9), [W10](#cite-W10)\]`)
    expect(out).toContain('[Smith 2020]')
  })
  it('skips code spans', () => {
    expect(linkifyCitations('`[W1]` and [W2]')).toBe(String.raw`${'`'}[W1]${'`'} and \[[W2](#cite-W2)\]`)
  })
  it('marks unverified ids', () => {
    expect(markInvalid('A [W1, W999]; B [W999] C [W9990]', ['W999'])).toBe('A [W1, ?W999]; B [?W999] C [W9990]')
  })
  it('collects cited ids in order', () => {
    expect(citedIds('[W2] [W1, W2] [?W3]')).toEqual(['W2', 'W1'])
  })
})

describe('geometry', () => {
  const square: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10]]
  it('point in polygon', () => {
    expect(pointInPolygon([5, 5], square)).toBe(true)
    expect(pointInPolygon([11, 5], square)).toBe(false)
  })
  it('bounds pads the span', () => {
    expect(bounds([[0, 0], [10, 20]], 0.1)).toEqual({ minX: -1, maxX: 11, minY: -2, maxY: 22 })
  })
})

describe('clusterColors', () => {
  it('gives the 8 largest clusters fixed slots and folds the rest into Other', () => {
    const cs = Array.from({ length: 10 }, (_, i) => ({ id: i, label: `c${i}`, size: i + 1 }))
    const { color, legend } = clusterColors(cs)
    expect(color(0)).toEqual(OTHER)
    expect(color(-1)).toEqual(UNCLUSTERED)
    expect(color(9)).not.toEqual(OTHER)
    expect(legend.map((l) => l.label)).toContain('Other clusters')
    expect(legend).toHaveLength(10)
  })
})
