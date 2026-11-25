// Citations look like [W123] or [W123, h0a1b2c3d4e]; the report stage marks unknown ids as ?id.
// Same pattern as litkb.query.CITE_RE, plus the optional '?' marker.

const PID = String.raw`\??(?:W\d+|h[0-9a-f]{10})`
const GROUP_RE = new RegExp(String.raw`\[(${PID}(?:\s*[,;]\s*${PID})*)\]`, 'g')

export const CITE_PREFIX = '#cite-'

/** Rewrite citation groups into Markdown links (`#cite-<id>`) so the renderer can make them clickable.
 *  Invalid ids (`?id`) stay plain text. Code spans and fenced blocks are left alone. */
export function linkifyCitations(md: string): string {
  return md
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) => (i % 2 === 1 ? part : part.replace(GROUP_RE, (_m, inner: string) => {
      const ids = inner.split(/\s*[,;]\s*/)
      const links = ids.map((id) => (id.startsWith('?') ? `?${id.slice(1)}` : `[${id}](${CITE_PREFIX}${id})`))
      return `\\[${links.join(', ')}\\]`
    })))
    .join('')
}

/** Prefix ids the server could not verify with '?' inside citation groups (as the report stage does). */
export function markInvalid(md: string, invalid: string[]): string {
  if (!invalid.length) return md
  const bad = new Set(invalid)
  return md.replace(GROUP_RE, (_m, inner: string) =>
    `[${inner.split(/(\s*[,;]\s*)/).map((part) => (bad.has(part) ? `?${part}` : part)).join('')}]`)
}

export function citedIds(md: string): string[] {
  const out: string[] = []
  for (const m of md.matchAll(GROUP_RE)) {
    for (const id of m[1].split(/\s*[,;]\s*/)) if (!id.startsWith('?') && !out.includes(id)) out.push(id)
  }
  return out
}
