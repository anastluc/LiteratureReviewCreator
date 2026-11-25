# litkb frontend

React 19 + Vite + TypeScript, Tailwind v4 + shadcn/ui (radix-nova), TanStack Query + Router, deck.gl.

```bash
npm install
npm run dev      # http://localhost:5173, proxies /api to `uv run lit serve` on :8000
npm run build    # -> dist/, served by FastAPI at http://localhost:8000
npm test         # Vitest: SSE parser, citation linking, lasso geometry, colour assignment
npm run lint     # oxlint
```

Pages (`src/pages`): **Ask** (streamed answers, citations open the paper panel, RQ/year filters, scope from a
map selection via `?papers=`), **Map** (UMAP/t-SNE, colour by cluster/RQ/year, cluster labels, citation links,
title highlight, lasso → "Ask about N papers"), **Papers** (sortable table with status filters), **Reports**
(Markdown with table of contents and clickable citations). The paper panel (`components/paper-panel.tsx`)
is shared by all pages; PDFs open in a new browser tab (`#page=N` for cited pages).

Windows note: `vite.config.ts` and `scripts/vitest.mjs` pin an upper-case drive letter. A shell started in
`c:\...` otherwise makes Vite bundle React twice and Vitest load itself twice.
