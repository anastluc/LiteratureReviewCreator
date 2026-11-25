"""RAG over the KB (§5.6): hybrid retrieval -> synthesis LLM citing [paper_id] -> citation validation."""
from __future__ import annotations

import re
from collections.abc import Iterator

from . import kb
from .db import short_citation
from .llm import get_provider
from .project import Project

PID = r"(?:W\d+|h[0-9a-f]{10})"
CITE_RE = re.compile(rf"\[({PID}(?:\s*[,;]\s*{PID})*)\]")

SYSTEM = """You answer questions about a research literature using ONLY the numbered source excerpts provided.
Rules:
- Every factual claim must be followed by its source paper id(s) in square brackets, e.g. [W2741809807] or
  [W123, h0a1b2c3d4e]. Use only ids that appear in the excerpts' headers.
- Excerpts labelled UNDERMIND SUMMARY are background syntheses; do not cite them, cite the papers instead.
- If the excerpts do not answer the question, say so plainly. Note disagreements between sources.
- Be concise and precise; write in Markdown."""


def format_context(chunks: list[dict], meta: dict[str, dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        if c["kind"] == "undermind_report":
            head = f"[{i}] UNDERMIND SUMMARY ({c['paper_id'].split(':', 1)[-1]}) — {c['section']}"
        else:
            m = meta.get(c["paper_id"], {})
            where = f"{c['section'] or c['kind']}" + (f", p.{c['page']}" if c.get("page") else "")
            head = f"[{i}] id={c['paper_id']} | {short_citation(m) if m else ''} | {m.get('title', '')} | {where}"
        parts.append(f"{head}\n{c['text']}")
    return "\n\n---\n\n".join(parts)


def paper_meta(project: Project, ids) -> dict[str, dict]:
    ids = [i for i in set(ids) if not str(i).startswith("undermind:")]
    if not ids:
        return {}
    with project.connect() as conn:
        rows = conn.execute(f"SELECT * FROM papers WHERE id IN ({','.join('?' * len(ids))})", ids)
        return {r["id"]: dict(r) for r in rows}


def extract_citations(text: str) -> list[str]:
    out = []
    for m in CITE_RE.finditer(text):
        out += [x.strip() for x in re.split(r"[,;]", m.group(1)) if x.strip()]
    return list(dict.fromkeys(out))


def validate_citations(text: str, allowed: set[str]) -> tuple[list[str], list[str]]:
    cited = extract_citations(text)
    return [c for c in cited if c in allowed], [c for c in cited if c not in allowed]


def retrieve(project: Project, question: str, *, paper_ids=None, rq_ids=None, year_min=None, year_max=None,
             k: int | None = None) -> tuple[list[dict], dict[str, dict]]:
    chunks = kb.search_chunks(project, question, k=k, paper_ids=paper_ids, rq_ids=rq_ids,
                              year_min=year_min, year_max=year_max)
    return chunks, paper_meta(project, [c["paper_id"] for c in chunks])


def _messages(question: str, chunks, meta):
    return [{"role": "user", "content": f"<sources>\n{format_context(chunks, meta)}\n</sources>\n\n"
                                        f"Question: {question}"}]


def sources_payload(chunks: list[dict], meta: dict[str, dict]) -> list[dict]:
    return [{"n": i, "chunk_id": c["chunk_id"], "paper_id": c["paper_id"], "kind": c["kind"],
             "section": c["section"], "page": c.get("page"), "text": c["text"],
             "title": meta.get(c["paper_id"], {}).get("title"),
             "citation": short_citation(meta[c["paper_id"]]) if c["paper_id"] in meta else None}
            for i, c in enumerate(chunks, 1)]


def answer(project: Project, question: str, **kw) -> dict:
    chunks, meta = retrieve(project, question, **kw)
    if not chunks:
        return {"answer": "No matching evidence in the knowledge base.", "sources": [], "citations": [],
                "invalid_citations": []}
    llm = get_provider("synthesis", project.config)
    text = llm.complete(_messages(question, chunks, meta), system=SYSTEM, max_tokens=3000)
    ok, bad = validate_citations(text, set(meta))
    return {"answer": text, "sources": sources_payload(chunks, meta), "citations": ok, "invalid_citations": bad}


def answer_stream(project: Project, question: str, **kw) -> Iterator[tuple[str, object]]:
    """Yields ('sources', [...]), then ('token', str)..., then ('done', {citations, invalid_citations})."""
    chunks, meta = retrieve(project, question, **kw)
    yield "sources", sources_payload(chunks, meta)
    if not chunks:
        yield "token", "No matching evidence in the knowledge base."
        yield "done", {"citations": [], "invalid_citations": []}
        return
    llm = get_provider("synthesis", project.config)
    buf = []
    for tok in llm.stream(_messages(question, chunks, meta), system=SYSTEM, max_tokens=3000):
        buf.append(tok)
        yield "token", tok
    ok, bad = validate_citations("".join(buf), set(meta))
    yield "done", {"citations": ok, "invalid_citations": bad}
