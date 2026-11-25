"""Stage 8: holistic report (D13, §5.8) — map-reduce over the KB.

1. By RQ: evidence = hybrid search over the RQ + sub-questions (restricted to the RQ's papers) + its Undermind
   report as an outline -> section (answer, evidence, consensus vs. disagreement, methods landscape, gaps).
2. Thematic appendix: one section per map cluster.
3. Executive summary + cross-cutting gaps / future directions.
4. Citation check (every [paper_id] must be an included paper) and an APA-like bibliography.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

import yaml

from .. import kb
from ..db import authors as paper_authors
from ..db import papers as db_papers
from ..llm import get_provider
from ..project import Project
from ..query import CITE_RE, extract_citations, format_context, paper_meta
from ..util import now_iso

Log = Callable[[str], None]

SYSTEM = """You are writing a rigorous, holistic academic literature review from a curated knowledge base.
Use ONLY the evidence provided. After every factual claim cite the supporting paper id(s) in square brackets,
e.g. [W2741809807] or [W123, h0a1b2c3d4e] — only ids that appear in the evidence headers or paper list.
Never cite UNDERMIND SUMMARY excerpts; they are outlines only. Synthesize across papers (compare, contrast,
weigh evidence quality) rather than summarising papers one by one. Write in Markdown, formal academic register."""

RQ_PROMPT = """Write the report section for this research question.

## {rq_id}: {question}
Sub-questions: {subs}
Context from the concept note:
{concept}

Use these Markdown sub-headings (### level): Answer · Evidence · Consensus and disagreement · Methods landscape ·
Gaps and open questions. Start "Answer" with a direct 2-4 sentence answer. Aim for 900-1500 words.

<papers_for_this_rq>
{papers}
</papers_for_this_rq>

<evidence>
{evidence}
</evidence>"""

CLUSTER_PROMPT = """Write one thematic-appendix section about this cluster of the literature map.

Cluster label: {label}
Description: {description}
Research questions of the review:
{rqs}

Cover, in ~400-700 words with ### sub-headings: What this cluster is about · Key papers and findings ·
How it relates to the research questions. Cite paper ids in square brackets.

<papers_in_cluster>
{papers}
</papers_in_cluster>

<evidence>
{evidence}
</evidence>"""

SUMMARY_PROMPT = """Below are the per-research-question sections of a literature review.
Write two parts in Markdown:
1. "## Executive summary": 300-500 words answering the overarching concept-note question across all RQs, with
   citations kept from the sections (same [paper_id] format).
2. "## Cross-cutting gaps and future directions": 5-10 bullet points spanning RQs, each grounded with citations.

<concept_note>
{concept}
</concept_note>

<sections>
{sections}
</sections>"""


def _paper_list(papers: list[dict], limit: int = 60) -> str:
    return "\n".join(f"- id={p['id']} | {p.get('year') or 'n.d.'} | {p['title']}" for p in papers[:limit])


def gather_evidence(project: Project, queries: list[str], limit: int, **filters) -> list[dict]:
    seen, out = set(), []
    per_q = max(8, limit // max(1, len(queries)) + 4)
    for qtext in queries:
        for c in kb.search_chunks(project, qtext, k=per_q, **filters):
            if c["chunk_id"] not in seen:
                seen.add(c["chunk_id"])
                out.append(c)
    return out[:limit]


def bibliography(meta: dict[str, dict], ids: list[str]) -> str:
    def entry(p: dict) -> str:
        names = paper_authors(p)

        def fmt(n: str) -> str:
            parts = n.split()
            return f"{parts[-1]}, {' '.join(x[0] + '.' for x in parts[:-1])}".strip(", ") if parts else n
        who = ", ".join(fmt(n) for n in names[:6]) + (", et al." if len(names) > 6 else "") or "Anon."
        link = f" https://doi.org/{p['doi']}" if p.get("doi") else (f" {p['landing_url']}" if p.get("landing_url")
                                                                     else "")
        venue = f" *{p['venue']}*." if p.get("venue") else ""
        return f"- **[{p['id']}]** {who} ({p.get('year') or 'n.d.'}). {p['title']}.{venue}{link}"
    rows = sorted((meta[i] for i in ids if i in meta), key=lambda p: (paper_authors(p) or ["~"])[0].split()[-1:])
    return "## References\n\n" + "\n".join(entry(p) for p in rows) + "\n"


def check_citations(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Mark citations to unknown ids as [?id] so they are visible; return (text, invalid ids)."""
    bad: list[str] = []

    def fix(m):
        ids = [x.strip() for x in re.split(r"[,;]", m.group(1))]
        out = []
        for i in ids:
            if i in allowed:
                out.append(i)
            else:
                bad.append(i)
                out.append(f"?{i}")
        return "[" + ", ".join(out) + "]"
    return CITE_RE.sub(fix, text), list(dict.fromkeys(bad))


def run(project: Project, *, log: Log = print, only: str | None = None) -> dict:
    """only: None (both reports), 'rq' or 'thematic'."""
    cfg = project.config
    rcfg = cfg["report"]
    llm = get_provider("synthesis", cfg)
    concept = project.concept.read_text(encoding="utf-8") if project.concept.exists() else ""
    rqs = (yaml.safe_load(project.rqs.read_text(encoding="utf-8")) or {}).get("rqs", []) \
        if project.rqs.exists() else []
    with project.connect() as conn:
        included = {p["id"]: p for p in db_papers(conn, "included=1")}
        rq_rank = {}
        for r in conn.execute("SELECT paper_id, rq_id, undermind_rank FROM paper_rq"):
            rq_rank.setdefault(r["rq_id"], []).append((r["undermind_rank"] or 10**6, r["paper_id"]))
    allowed = set(included)
    project.reports_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {"generated_at": now_iso()}

    def write(name: str, title: str, body: str) -> list[str]:
        cited = [c for c in extract_citations(body) if c in allowed]
        body, bad = check_citations(body, allowed)
        text = f"# {title}\n\n*Generated {now_iso()} by litkb from project `{project.slug}` " \
               f"({len(included)} papers).*\n\n{body.strip()}\n\n{bibliography(included, cited)}"
        if bad:
            text += "\n> **Citation check:** unknown ids marked `[?id]`: " + ", ".join(bad) + "\n"
        (project.reports_dir / name).write_text(text, encoding="utf-8")
        log(f"Wrote reports/{name}: {len(cited)} papers cited, {len(bad)} invalid citations")
        return bad

    if only in (None, "rq"):
        sections = []
        for rq in rqs:
            rid = rq["id"]
            log(f"Section {rid}: gathering evidence")
            queries = [rq["question"], *rq.get("sub_questions", [])]
            ev = gather_evidence(project, queries, rcfg["evidence_chunks_per_rq"], rq_ids=[rid])
            meta = {**paper_meta(project, [c["paper_id"] for c in ev])}
            ranked = [included[pid] for _, pid in sorted(rq_rank.get(rid, [])) if pid in included]
            prompt = RQ_PROMPT.format(rq_id=rid, question=rq["question"],
                                      subs="; ".join(rq.get("sub_questions", [])) or "—",
                                      concept=concept[:3000], papers=_paper_list(ranked),
                                      evidence=format_context(ev, meta))
            body = llm.complete([{"role": "user", "content": prompt}], system=SYSTEM,
                                max_tokens=rcfg["max_tokens_section"])
            sections.append(f"## {rid}. {rq['question']}\n\n{body.strip()}")
        log("Executive summary + cross-cutting gaps")
        summary = llm.complete([{"role": "user", "content": SUMMARY_PROMPT.format(
            concept=concept[:4000], sections="\n\n".join(sections))}], system=SYSTEM,
            max_tokens=rcfg["max_tokens_section"])
        body = summary.strip() + "\n\n" + "\n\n".join(sections)
        result["report_by_rq_invalid"] = write("report_by_rq.md", "Literature review — by research question", body)

    clusters_path = project.map_dir / "clusters.json"
    if only in (None, "thematic"):
        if not clusters_path.exists():
            log("No map/clusters.json; run `lit map` for the thematic appendix")
        else:
            clusters = json.loads(clusters_path.read_text(encoding="utf-8"))
            members: dict[int, list[str]] = {}
            for p in kb.all_papers(project):
                members.setdefault(p.get("cluster_id") if p.get("cluster_id") is not None else -1,
                                   []).append(p["paper_id"])
            rq_list = "\n".join(f"- {r['id']}: {r['question']}" for r in rqs) or "—"
            parts = []
            for c in clusters:
                ids = [i for i in members.get(c["id"], []) if i in included]
                if not ids:
                    continue
                log(f"Cluster {c['id']}: {c['label']}")
                ev = gather_evidence(project, [c["label"] + ". " + c.get("description", "")],
                                     rcfg["evidence_chunks_per_cluster"], paper_ids=ids)
                meta = paper_meta(project, [e["paper_id"] for e in ev])
                cps = sorted((included[i] for i in ids), key=lambda p: -(p.get("cited_by_count") or 0))
                body = llm.complete([{"role": "user", "content": CLUSTER_PROMPT.format(
                    label=c["label"], description=c.get("description", ""), rqs=rq_list,
                    papers=_paper_list(cps, 40), evidence=format_context(ev, meta))}], system=SYSTEM,
                    max_tokens=rcfg["max_tokens_section"] // 2)
                parts.append(f"## Theme {c['id']}: {c['label']} ({len(ids)} papers)\n\n{body.strip()}")
            result["report_thematic_invalid"] = write("report_thematic.md",
                                                      "Thematic appendix — clusters of the literature map",
                                                      "\n\n".join(parts))
    return result
