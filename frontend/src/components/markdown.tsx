import { useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { usePaperPanel } from '@/components/paper-panel'
import { CITE_PREFIX, linkifyCitations } from '@/lib/citations'
import { cn } from '@/lib/utils'

export const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')

const text = (children: unknown): string =>
  Array.isArray(children) ? children.map(text).join('') : typeof children === 'string' ? children : ''

/** Markdown with clickable [paper_id] citations (they open the paper panel) and anchored headings. */
export function Markdown({ source, className, titles }: {
  source: string; className?: string; titles?: Map<string, string>
}) {
  const panel = usePaperPanel()
  const md = useMemo(() => linkifyCitations(source), [source])
  const components: Components = useMemo(() => ({
    a: ({ href, children, ...rest }) => {
      if (href?.startsWith(CITE_PREFIX)) {
        const id = href.slice(CITE_PREFIX.length)
        return (
          <button type="button" title={titles?.get(id) ?? id} onClick={() => panel.open(id)}
                  className="rounded px-0.5 font-mono text-[0.85em] text-primary underline decoration-dotted underline-offset-2 hover:bg-accent">
            {children}
          </button>
        )
      }
      return <a href={href} target="_blank" rel="noreferrer" {...rest}>{children}</a>
    },
    h2: ({ children }) => <h2 id={slugify(text(children))}>{children}</h2>,
    h3: ({ children }) => <h3 id={slugify(text(children))}>{children}</h3>,
  }), [panel, titles])
  return (
    <div className={cn('md max-w-none', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{md}</ReactMarkdown>
    </div>
  )
}
