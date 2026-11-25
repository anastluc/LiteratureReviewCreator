# LiteratureReviewCreator (`litkb`)

Concept note → research questions → Undermind → paper retrieval → queryable knowledge base → holistic report + interactive literature map.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and decision log.

## Setup
```bash
uv sync                      # Python 3.12 env + deps (first run downloads torch, a few hundred MB)
cp .env.example .env         # OPENROUTER_API_KEY (+ LITKB_MODEL), UNPAYWALL_EMAIL, EZPROXY_PREFIX
docker compose up -d grobid  # PDF parser (Docker Desktop must be running)
uv run playwright install chromium   # for the paywall browser fallback
npm --prefix frontend install && npm --prefix frontend run build   # web UI (Node 20+)
```

## Workflow
```bash
uv run lit new my-topic           # then edit projects/my-topic/concept.md
uv run lit rq draft -p my-topic   # or Claude Code skill: generate-rqs
#   👤 edit rqs.draft.yaml
uv run lit rq approve -p my-topic
#   Claude Code skill: undermind-search
uv run lit merge run -p my-topic
#   👤 prune papers_review.csv
uv run lit merge approve -p my-topic
uv run lit login                  # once: sign in through university SSO
uv run lit retrieve -p my-topic
uv run lit parse -p my-topic
uv run lit index -p my-topic
uv run lit map -p my-topic
uv run lit report -p my-topic     # or skill: write-report
uv run lit serve                  # web UI at http://localhost:8000 (Ask, Map, Papers, Reports)
```

Helpers: `lit status -p <slug>` (progress), `lit run -p <slug>` (run every due stage, stopping at the 👤
checkpoints), `lit query -p <slug> "question" [--rq RQ1] [--no-llm] [--json]` (ask the KB).
Every stage is resumable; `retrieve`/`parse` take `--retry`, `index` takes `--rebuild`.
PDFs the pipeline can't get are listed in `reports/missing_papers.csv`; drop them into
`projects/<slug>/inbox/` (any filename; the DOI with `/`→`_` matches fastest) and rerun `lit retrieve`.

## Tests
```bash
uv run pytest      # offline: HTTP, LLM and embedding models are mocked
uv run ruff check src tests
npm --prefix frontend test        # frontend unit tests (Vitest)
```

## Frontend development
`npm --prefix frontend run dev` serves the UI with hot reload at http://localhost:5173 and proxies `/api` to a
running `uv run lit serve`. See [frontend/README.md](frontend/README.md).
