"""Stage 3: merge per-RQ Undermind paper lists -> dedupe -> OpenAlex enrichment -> papers_review.csv.

`approve` loads the human-pruned review CSV into papers.sqlite (checkpoint 2).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from ..http import PoliteClient
from ..openalex import OpenAlex, work_to_paper
from ..project import Project
from ..util import fallback_paper_id, normalize_doi, normalize_title

# Column aliases accepted in undermind/<RQ>/papers.csv (Undermind exports and manual CSVs vary).
ALIASES = {
    "title": ["title", "paper title", "name"],
    "doi": ["doi", "doi_url"],
    "url": ["url", "link", "paper url", "source url"],
    "authors": ["authors", "author", "author names"],
    "year": ["year", "publication year", "pub year", "date", "publication date"],
    "venue": ["venue", "journal", "source", "publication"],
    "abstract": ["abstract", "summary"],
    "rank": ["rank", "#", "position", "index"],
    "score": ["score", "relevance", "relevance score", "relevance_score", "probability", "match"],
    "cite_key": ["cite_key", "citekey", "cite key", "key"],
}

REVIEW_COLUMNS = ["include", "id", "title", "year", "venue", "authors", "rq_ids", "best_rank", "undermind_ranks",
                  "undermind_scores", "cited_by_count", "is_oa", "doi", "openalex_id", "arxiv_id", "pmid",
                  "oa_url", "landing_url", "abstract", "referenced_works", "undermind_cite_keys"]
TRUTHY = {"1", "y", "yes", "true", "x", "include", "keep"}


def _pick(cols: dict[str, str], field: str) -> str | None:
    for a in ALIASES[field]:
        if a in cols:
            return cols[a]
    return None


def _year(v) -> int | None:
    m = re.search(r"(1[89]\d\d|20\d\d)", str(v or ""))
    return int(m.group(1)) if m else None


def _authors(v) -> list[str]:
    """'A; B; C' (what the undermind-search skill writes) or 'A, B and C'."""
    v = (v or "").strip()
    parts = v.split(";") if ";" in v else re.split(r",|\band\b", v)
    return [a.strip() for a in parts if a.strip()]


def _score(v) -> float | None:
    try:
        s = str(v).strip().rstrip("%")
        f = float(s)
        return f / 100 if "%" in str(v) or f > 1 else f
    except (TypeError, ValueError):
        return None


def read_undermind_csvs(undermind_dir: Path) -> list[dict]:
    """All rows from undermind/<RQ>/*.csv, normalised, with their rq_id."""
    rows: list[dict] = []
    for rq_dir in sorted(p for p in undermind_dir.iterdir() if p.is_dir()) if undermind_dir.exists() else []:
        for csv in sorted(rq_dir.glob("*.csv")):
            df = pd.read_csv(csv, dtype=str, keep_default_na=False, encoding_errors="replace")
            cols = {c.strip().lower(): c for c in df.columns}
            get = {f: _pick(cols, f) for f in ALIASES}
            if not get["title"]:
                raise ValueError(f"{csv}: no title column (have {list(df.columns)})")
            for i, r in enumerate(df.to_dict("records"), 1):
                title = (r.get(get["title"]) or "").strip()
                if not title:
                    continue
                doi = normalize_doi(r.get(get["doi"]) if get["doi"] else None) or \
                    normalize_doi(r.get(get["url"]) if get["url"] else None)
                rank = r.get(get["rank"]) if get["rank"] else None
                rows.append({
                    "rq_id": rq_dir.name, "title": title, "doi": doi,
                    "url": (r.get(get["url"]) or None) if get["url"] else None,
                    "authors": _authors(r.get(get["authors"]) if get["authors"] else None),
                    "year": _year(r.get(get["year"])) if get["year"] else None,
                    "venue": (r.get(get["venue"]) or None) if get["venue"] else None,
                    "abstract": (r.get(get["abstract"]) or None) if get["abstract"] else None,
                    "rank": int(rank) if rank and str(rank).strip().isdigit() else i,
                    "score": _score(r.get(get["score"])) if get["score"] else None,
                    "cite_key": (r.get(get["cite_key"]) or None) if get["cite_key"] else None,
                })
    return rows


def dedupe(rows: list[dict], threshold: int = 93) -> list[dict]:
    """Group rows into papers: same DOI, or (when a DOI is missing on either side) near-identical title."""
    groups: list[dict] = []
    by_doi: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    for r in rows:
        nt = normalize_title(r["title"])
        g = by_doi.get(r["doi"]) if r["doi"] else None
        if g is None:
            g = by_title.get(nt)
        if g is None and nt:
            for t, cand in by_title.items():
                if r["doi"] and cand["doi"] and r["doi"] != cand["doi"]:
                    continue
                if fuzz.token_sort_ratio(nt, t) >= threshold and \
                        (not r["year"] or not cand["year"] or abs(r["year"] - cand["year"]) <= 1):
                    g = cand
                    break
        if g is None:
            g = {"title": r["title"], "doi": r["doi"], "year": r["year"], "venue": r["venue"],
                 "authors": r["authors"], "abstract": r["abstract"], "url": r["url"], "rqs": {},
                 "cite_keys": set()}
            groups.append(g)
        for k in ("doi", "year", "venue", "abstract", "url"):
            g[k] = g[k] or r[k]
        g["authors"] = g["authors"] or r["authors"]
        prev = g["rqs"].get(r["rq_id"])
        if prev is None or r["rank"] < prev[0]:
            g["rqs"][r["rq_id"]] = (r["rank"], r["score"])
        if r["cite_key"]:
            g["cite_keys"].add(r["cite_key"])
        if g["doi"]:
            by_doi[g["doi"]] = g
        if nt:
            by_title[nt] = g
    return groups


async def enrich(groups: list[dict], batch_size: int = 50, threshold: int = 93,
                 client: PoliteClient | None = None) -> None:
    """Attach OpenAlex metadata (g['work']) by DOI batch, then by title search for the rest."""
    own = client is None
    client = client or PoliteClient(delay_s=0.15)
    try:
        oa = OpenAlex(client)
        with_doi = [g for g in groups if g["doi"]]
        for i in range(0, len(with_doi), batch_size):
            batch = with_doi[i:i + batch_size]
            found = await oa.by_dois([g["doi"] for g in batch])
            for g in batch:
                g["work"] = found.get(g["doi"])
        missing = [g for g in groups if not g.get("work")]
        sem = asyncio.Semaphore(4)

        async def by_title(g):
            async with sem:
                try:
                    g["work"] = await oa.by_title(g["title"], g["year"], threshold)
                except Exception:  # enrichment is best-effort
                    g["work"] = None
        await asyncio.gather(*(by_title(g) for g in missing))
    finally:
        if own:
            await client.aclose()


def to_review_rows(groups: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for g in groups:
        p = work_to_paper(g["work"]) if g.get("work") else {}
        doi = p.get("doi") or g["doi"]
        pid = p.get("id") or fallback_paper_id(doi, g["title"])
        row = merged.get(pid)
        if row is None:
            row = merged[pid] = {
                "include": 1, "id": pid, "title": p.get("title") or g["title"], "year": p.get("year") or g["year"],
                "venue": p.get("venue") or g["venue"],
                "authors": "; ".join(json.loads(p["authors_json"]) if p.get("authors_json") else g["authors"]),
                "cited_by_count": p.get("cited_by_count"), "is_oa": p.get("is_oa"), "doi": doi,
                "openalex_id": p.get("openalex_id"), "arxiv_id": p.get("arxiv_id"), "pmid": p.get("pmid"),
                "oa_url": p.get("oa_url"), "landing_url": p.get("landing_url") or g["url"],
                "abstract": p.get("abstract") or g["abstract"],
                "referenced_works": " ".join(json.loads(p.get("referenced_works_json") or "[]")),
                "_rqs": {}, "_keys": set()}
        for rq, (rank, score) in g["rqs"].items():
            prev = row["_rqs"].get(rq)
            if prev is None or rank < prev[0]:
                row["_rqs"][rq] = (rank, score)
        row["_keys"] |= g["cite_keys"]
    out = []
    for row in merged.values():
        rqs = dict(sorted(row.pop("_rqs").items()))
        row["rq_ids"] = ";".join(rqs)
        row["best_rank"] = min(r for r, _ in rqs.values())
        row["undermind_ranks"] = ";".join(f"{k}:{r}" for k, (r, _) in rqs.items())
        row["undermind_scores"] = ";".join(f"{k}:{s:.3f}" for k, (_, s) in rqs.items() if s is not None)
        row["undermind_cite_keys"] = ";".join(sorted(row.pop("_keys")))
        out.append(row)
    out.sort(key=lambda r: (-len(r["rq_ids"].split(";")), r["best_rank"], r["title"].lower()))
    return out


def merge(project: Project) -> pd.DataFrame:
    cfg = project.config["merge"]
    rows = read_undermind_csvs(project.undermind_dir)
    if not rows:
        raise FileNotFoundError(f"No CSVs under {project.undermind_dir}/<RQ>/ — run the undermind-search skill")
    groups = dedupe(rows, cfg["title_match_threshold"])
    asyncio.run(enrich(groups, cfg["openalex_batch_size"], cfg["title_match_threshold"]))
    df = pd.DataFrame(to_review_rows(groups), columns=REVIEW_COLUMNS)
    if project.papers_review.exists():  # keep earlier include/exclude decisions on re-run
        old = pd.read_csv(project.papers_review, dtype=str, keep_default_na=False)
        if "id" in old and "include" in old:
            prev = dict(zip(old["id"], old["include"]))
            df["include"] = [prev.get(i, inc) for i, inc in zip(df["id"], df["include"])]
    df.to_csv(project.papers_review, index=False, encoding="utf-8")
    return df


def approve(project: Project) -> dict:
    from ..db import upsert_paper
    if not project.papers_review.exists():
        raise FileNotFoundError(f"{project.papers_review} not found; run `lit merge run` first")
    df = pd.read_csv(project.papers_review, dtype=str, keep_default_na=False)
    na = lambda v: v if v not in ("", None) else None
    with project.connect() as conn:
        known_rqs = {r[0] for r in conn.execute("SELECT id FROM rqs")}
        ids = set()
        for r in df.to_dict("records"):
            pid = r["id"]
            ids.add(pid)
            upsert_paper(conn, {
                "id": pid, "doi": na(r["doi"]), "openalex_id": na(r["openalex_id"]), "arxiv_id": na(r["arxiv_id"]),
                "pmid": na(r["pmid"]), "title": r["title"], "abstract": na(r["abstract"]),
                "year": int(float(r["year"])) if na(r["year"]) else None, "venue": na(r["venue"]),
                "authors_json": json.dumps([a.strip() for a in r["authors"].split(";") if a.strip()]),
                "cited_by_count": int(float(r["cited_by_count"])) if na(r["cited_by_count"]) else None,
                "is_oa": int(float(r["is_oa"])) if na(r["is_oa"]) else None,
                "oa_url": na(r["oa_url"]), "landing_url": na(r["landing_url"]),
                "referenced_works_json": json.dumps(r["referenced_works"].split()),
                "included": int(str(r["include"]).strip().lower() in TRUTHY),
            })
            ranks = dict(x.split(":", 1) for x in r["undermind_ranks"].split(";") if ":" in x)
            scores = dict(x.split(":", 1) for x in r["undermind_scores"].split(";") if ":" in x)
            conn.execute("DELETE FROM paper_rq WHERE paper_id=?", (pid,))
            for rq in filter(None, r["rq_ids"].split(";")):
                if rq not in known_rqs:  # tolerate RQ folders created before `lit rq approve`
                    conn.execute("INSERT OR IGNORE INTO rqs (id, text) VALUES (?, ?)", (rq, rq))
                    known_rqs.add(rq)
                conn.execute("INSERT INTO paper_rq VALUES (?,?,?,?)",
                             (pid, rq, int(ranks[rq]) if rq in ranks else None,
                              float(scores[rq]) if rq in scores else None))
        stale = [i for (i,) in conn.execute("SELECT id FROM papers") if i not in ids]
        conn.executemany("UPDATE papers SET included=0 WHERE id=?", [(i,) for i in stale])
        # intra-corpus citation edges among included papers
        conn.execute("DELETE FROM citations")
        inc = {r["id"]: json.loads(r["referenced_works_json"] or "[]")
               for r in conn.execute("SELECT id, referenced_works_json FROM papers WHERE included=1")}
        edges = [(src, dst) for src, refs in inc.items() for dst in refs if dst in inc and dst != src]
        conn.executemany("INSERT OR IGNORE INTO citations VALUES (?,?)", edges)
        n_inc = conn.execute("SELECT COUNT(*) FROM papers WHERE included=1").fetchone()[0]
    return {"papers": len(ids), "included": n_inc, "citations": len(edges)}


def run(project: Project, action: str, **kw):
    return {"merge": merge, "approve": approve}[action](project, **kw)
