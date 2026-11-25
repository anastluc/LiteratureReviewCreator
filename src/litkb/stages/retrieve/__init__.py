"""Stage 4: retrieve PDFs (ARCHITECTURE.md §5.3).

Phase 0  inbox/ — manually dropped PDFs are matched to papers and ingested (pdf_source = manual).
Phase 1  open-access resolver chain (async): unpaywall → openalex → semantic_scholar → arxiv → biorxiv.
Phase 2  browser fallback (Playwright persistent profile + EZproxy), sequential and rate-limited.
Then     reports/missing_papers.csv lists everything still without a PDF.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from rapidfuzz import fuzz

from ...db import papers as db_papers
from ...db import update_paper
from ...http import PoliteClient
from ...project import Project
from ...util import normalize_doi, normalize_title, pdf_cache_key
from .validate import check_pdf, citation_pdf_url, first_pages_text, looks_like_pdf

Log = Callable[[str], None]
RETRYABLE = ("not_found", "paywalled", "failed")


class Resolver(Protocol):
    name: str

    async def find_pdf_urls(self, paper: dict) -> list[str]: ...


def build_resolvers(order: list[str], client: PoliteClient, cfg: dict) -> list[Resolver]:
    from .openalex import OpenAlexResolver
    from .preprints import ArxivResolver, BiorxivResolver
    from .semantic_scholar import SemanticScholarResolver
    from .unpaywall import UnpaywallResolver
    factories = {
        "unpaywall": lambda: UnpaywallResolver(client),
        "openalex": lambda: OpenAlexResolver(client),
        "semantic_scholar": lambda: SemanticScholarResolver(client),
        "arxiv": lambda: ArxivResolver(client, cfg["merge"]["title_match_threshold"]),
        "biorxiv": lambda: BiorxivResolver(client),
    }
    unknown = [n for n in order if n not in factories and n != "browser"]
    if unknown:
        raise ValueError(f"Unknown retrieve sources: {unknown}")
    return [factories[n]() for n in order if n in factories]


def store_pdf(project: Project, paper: dict, data: bytes) -> Path:
    path = project.pdf_cache_dir / f"{pdf_cache_key(paper.get('doi'), paper['id'])}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _mark_ok(conn, paper: dict, path: Path, source: str) -> None:
    update_paper(conn, paper["id"], pdf_status="ok", pdf_source=source, pdf_path=str(path),
                 pdf_sha1=hashlib.sha1(path.read_bytes()).hexdigest())


def _attempt(conn, paper_id: str, source: str, url: str, status: int | None, outcome: str) -> None:
    conn.execute("INSERT INTO retrieval_attempts (paper_id, source, url, http_status, outcome) VALUES (?,?,?,?,?)",
                 (paper_id, source, url, status, outcome))


# ---------------------------------------------------------------- phase 0: shared cache + inbox
def reuse_cache(project: Project, conn, paper: dict) -> bool:
    path = project.pdf_cache_dir / f"{pdf_cache_key(paper.get('doi'), paper['id'])}.pdf"
    if path.exists() and looks_like_pdf(path.read_bytes()[:1024]):
        _mark_ok(conn, paper, path, paper.get("pdf_source") or "cache")
        return True
    return False


def ingest_inbox(project: Project, conn, log: Log) -> int:
    """Match inbox/*.pdf to papers without a PDF by DOI (filename or text) or title, then ingest."""
    files = sorted(project.inbox_dir.glob("*.pdf")) if project.inbox_dir.exists() else []
    if not files:
        return 0
    todo = db_papers(conn, "included=1 AND pdf_status != 'ok'")
    by_doi = {p["doi"]: p for p in todo if p["doi"]}
    n = 0
    for f in files:
        data = f.read_bytes()
        if not looks_like_pdf(data):
            continue
        text = first_pages_text(data, pages=2)
        doi = normalize_doi(f.stem.replace("_", "/")) or normalize_doi(text)
        match = by_doi.get(doi) if doi else None
        if match is None:
            hay = normalize_title(f.stem.replace("_", " ") + " " + text[:6000])
            scored = [(fuzz.partial_ratio(normalize_title(p["title"]), hay), p) for p in todo]
            best = max(scored, key=lambda x: x[0], default=(0, None))
            match = best[1] if best[0] >= 90 else None
        if match is None:
            log(f"  inbox: could not match {f.name}")
            continue
        path = store_pdf(project, match, data)
        _mark_ok(conn, match, path, "manual")
        _attempt(conn, match["id"], "manual", str(f), None, "ok")
        done = project.inbox_dir / "ingested"
        done.mkdir(exist_ok=True)
        shutil.move(str(f), done / f.name)
        todo = [p for p in todo if p["id"] != match["id"]]
        log(f"  inbox: {f.name} -> {match['id']}")
        n += 1
    return n


# ---------------------------------------------------------------- phase 1: open access
async def download(client: PoliteClient, url: str, paper: dict, rcfg: dict, depth: int = 0
                   ) -> tuple[bytes | None, int | None, str]:
    try:
        r = await client.get(url, raise_for_status=False,
                             headers={"Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8"})
    except Exception as e:
        return None, None, f"error:{type(e).__name__}"
    if r.status_code != 200:
        return None, r.status_code, "http_error"
    data = r.content
    if not looks_like_pdf(data) and depth == 0 and "html" in r.headers.get("content-type", ""):
        if (nxt := citation_pdf_url(r.text)) and nxt != url:
            return await download(client, str(r.url.join(nxt)), paper, rcfg, depth=1)
    ok, why = check_pdf(data, paper.get("title"), min_bytes=rcfg["min_pdf_bytes"],
                        threshold=rcfg["title_check_threshold"])
    return (data if ok else None), r.status_code, why


async def resolve_one(project: Project, conn, paper: dict, resolvers: list[Resolver],
                      client: PoliteClient, rcfg: dict, log: Log) -> bool:
    tried: set[str] = set()
    for res in resolvers:
        try:
            urls = await res.find_pdf_urls(paper)
        except Exception as e:
            _attempt(conn, paper["id"], res.name, "", None, f"resolver_error:{type(e).__name__}")
            continue
        for url in urls:
            if url in tried:
                continue
            tried.add(url)
            data, status, outcome = await download(client, url, paper, rcfg)
            _attempt(conn, paper["id"], res.name, url, status, outcome)
            if data:
                _mark_ok(conn, paper, store_pdf(project, paper, data), res.name)
                if paper.get("arxiv_id"):
                    update_paper(conn, paper["id"], arxiv_id=paper["arxiv_id"])
                log(f"  ok  {paper['id']} via {res.name}")
                return True
    if not tried:
        _attempt(conn, paper["id"], "open_access", "", None, "no_candidates")
    status = "paywalled" if paper.get("is_oa") == 0 else "not_found"
    update_paper(conn, paper["id"], pdf_status=status)
    log(f"  --  {paper['id']} {status}")
    return False


async def open_access_phase(project: Project, conn, todo: list[dict], log: Log,
                            transport=None) -> int:
    cfg = project.config
    rcfg = cfg["retrieve"]
    async with PoliteClient(delay_s=rcfg["per_host_delay_s"], timeout_s=rcfg["timeout_s"],
                            transport=transport) as client:
        resolvers = build_resolvers(rcfg["source_order"], client, cfg)
        sem = asyncio.Semaphore(rcfg["concurrency"])

        async def one(p):
            async with sem:
                return await resolve_one(project, conn, p, resolvers, client, rcfg, log)
        results = await asyncio.gather(*(one(p) for p in todo))
    return sum(results)


# ---------------------------------------------------------------- phase 2: browser
def browser_phase(project: Project, conn, todo: list[dict], log: Log) -> int:
    from .browser import BrowserFetcher
    rcfg = project.config["retrieve"]
    n = 0
    with BrowserFetcher(project.browser_profile_dir, headless=rcfg["browser_headless"],
                        delay_s=rcfg["browser_delay_s"], max_per_hour=rcfg["browser_max_per_hour"],
                        min_bytes=rcfg["min_pdf_bytes"], title_threshold=rcfg["title_check_threshold"],
                        log=log) as bf:
        for p in todo:
            data, _, attempts = bf.fetch(p)
            for url, outcome in attempts:
                _attempt(conn, p["id"], "browser", url, None, outcome)
            if data:
                _mark_ok(conn, p, store_pdf(project, p, data), "browser")
                log(f"  ok  {p['id']} via browser")
                n += 1
            else:
                log(f"  --  {p['id']} browser: {attempts[-1][1] if attempts else 'failed'}")
            conn.commit()
    return n


def write_missing(project: Project, conn) -> int:
    from .browser import proxied
    rows = db_papers(conn, "included=1 AND pdf_status != 'ok'")
    project.reports_dir.mkdir(parents=True, exist_ok=True)
    with (project.reports_dir / "missing_papers.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "year", "doi", "status", "doi_url", "proxy_url", "inbox_filename"])
        for p in rows:
            doi_url = f"https://doi.org/{p['doi']}" if p["doi"] else (p["landing_url"] or "")
            w.writerow([p["id"], p["title"], p["year"], p["doi"] or "", p["pdf_status"], doi_url,
                        proxied(doi_url) if doi_url else "",
                        (re.sub(r"[^\w.-]", "_", p["doi"].replace("/", "_")) if p["doi"] else p["id"]) + ".pdf"])
    return len(rows)


def run(project: Project, *, browser: bool = True, retry: bool = False, log: Log = print,
        transport=None) -> dict:
    project.ensure_dirs()
    rcfg = project.config["retrieve"]
    with project.connect() as conn:
        statuses = ("pending",) + (RETRYABLE if retry else ())
        q = f"included=1 AND pdf_status IN ({','.join('?' * len(statuses))})"
        todo = [p for p in db_papers(conn, q, statuses) if not reuse_cache(project, conn, p)]
        conn.commit()
        n_inbox = ingest_inbox(project, conn, log)
        conn.commit()
        todo = [p for p in db_papers(conn, q, statuses)]
        log(f"Open-access pass over {len(todo)} papers")
        n_oa = asyncio.run(open_access_phase(project, conn, todo, log, transport)) if todo else 0
        conn.commit()
        n_br = 0
        if browser and rcfg["browser_enabled"] and "browser" in rcfg["source_order"]:
            tried = {r[0] for r in conn.execute("SELECT DISTINCT paper_id FROM retrieval_attempts "
                                                "WHERE source='browser'")}
            remaining = [p for p in db_papers(conn, "included=1 AND pdf_status IN ('not_found','paywalled')")
                         if (retry or p["id"] not in tried) and (p["doi"] or p["landing_url"])]
            if remaining:
                log(f"Browser pass over {len(remaining)} papers (run `lit login` first if you haven't)")
                n_br = browser_phase(project, conn, remaining, log)
        n_missing = write_missing(project, conn)
    log(f"Done: inbox {n_inbox}, open access {n_oa}, browser {n_br}; still missing {n_missing} "
        f"(see reports/missing_papers.csv; drop PDFs into {project.inbox_dir} and rerun)")
    return {"inbox": n_inbox, "open_access": n_oa, "browser": n_br, "missing": n_missing}
