"""Stage 6: build the LanceDB KB.

- `papers`: SPECTER2(title [SEP] abstract) for every included paper (vectors reused across runs).
- `chunks`: bge vectors for full-text chunks (GROBID JSON), abstracts, and Undermind reports; FTS index on text.
Incremental: only papers with index_status = pending are (re)chunked; RQ assignments are refreshed every run.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from lancedb.index import FTS

from .. import kb
from ..chunking import chunk_document, chunk_markdown, chunk_text
from ..db import papers as db_papers
from ..db import rq_map
from ..embed import chunk_embedder, paper_embedder, paper_text
from ..project import Project

Log = Callable[[str], None]


def _paper_chunks(project: Project, p: dict, rqs: list[str], ccfg: dict) -> list[dict]:
    rows = []
    if p.get("abstract"):
        for i, t in enumerate(chunk_text(p["abstract"], ccfg)):
            rows.append({"chunk_id": f"{p['id']}:abstract:{i}", "kind": "abstract", "section": "Abstract",
                         "page": None, "text": t})
    doc_path = project.parsed_dir / f"{p['id']}.json"
    if p.get("parse_status") == "ok" and doc_path.exists():
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        for i, c in enumerate(chunk_document(doc, ccfg)):
            rows.append({"chunk_id": f"{p['id']}:fulltext:{i}", "kind": "fulltext", **c})
    for r in rows:
        r.update(paper_id=p["id"], rq_ids=rqs, year=p.get("year"))
    return rows


def _report_chunks(project: Project, ccfg: dict) -> list[dict]:
    rows = []
    for rq_dir in sorted(d for d in project.undermind_dir.iterdir() if d.is_dir()) \
            if project.undermind_dir.exists() else []:
        rep = rq_dir / "report.md"
        if not rep.exists():
            continue
        for i, c in enumerate(chunk_markdown(rep.read_text(encoding="utf-8"), ccfg)):
            rows.append({"chunk_id": f"undermind:{rq_dir.name}:{i}", "paper_id": f"undermind:{rq_dir.name}",
                         "kind": "undermind_report", "section": c["section"], "page": None, "text": c["text"],
                         "rq_ids": [rq_dir.name], "year": None})
    return rows


def _embed_chunks(rows: list[dict], cfg: dict, titles: dict[str, str]) -> list[dict]:
    if not rows:
        return rows
    emb = chunk_embedder(cfg)
    # prepend paper title + section so short chunks keep their context in vector space
    texts = [f"{titles.get(r['paper_id'], '')}. {r['section'] or ''}\n{r['text']}" for r in rows]
    vecs = emb.embed(texts)
    for r, v in zip(rows, vecs):
        r["vector"] = v.tolist()
    return rows


def run(project: Project, *, rebuild: bool = False, log: Log = print) -> dict:
    cfg = project.config
    ccfg = cfg["chunk"]
    db = kb.connect(project)
    if rebuild:
        for name in (kb.PAPERS, kb.CHUNKS):
            if name in kb.table_names(db):
                db.drop_table(name)
    with project.connect() as conn:
        if rebuild:
            conn.execute("UPDATE papers SET index_status='pending'")
        included = db_papers(conn, "included=1")
        rqs = rq_map(conn)
        excluded = [r[0] for r in conn.execute("SELECT id FROM papers WHERE included=0")]

        # ---- papers table (SPECTER2), reusing vectors already computed
        pemb = paper_embedder(cfg)
        ptab = kb.ensure_table(db, kb.PAPERS, kb.papers_schema(pemb.dim))
        existing = {r["paper_id"]: r for r in ptab.to_arrow().to_pylist()}
        new = [p for p in included if p["id"] not in existing]
        log(f"SPECTER2: embedding {len(new)} new papers ({len(existing)} cached)")
        vecs = pemb.embed([paper_text(pemb, p["title"], p["abstract"]) for p in new]) if new else []
        fresh = {p["id"]: v.tolist() for p, v in zip(new, vecs)}
        rows = [{"paper_id": p["id"], "vector": fresh.get(p["id"]) or existing[p["id"]]["vector"],
                 "title": p["title"], "abstract": p["abstract"], "year": p["year"], "rq_ids": rqs.get(p["id"], []),
                 "cluster_id": (existing.get(p["id"]) or {}).get("cluster_id")} for p in included]
        db.drop_table(kb.PAPERS)
        ptab = db.create_table(kb.PAPERS, schema=kb.papers_schema(pemb.dim))
        if rows:
            ptab.add(rows)

        # ---- chunks table (bge)
        cemb = chunk_embedder(cfg)
        ctab = kb.ensure_table(db, kb.CHUNKS, kb.chunks_schema(cemb.dim))
        titles = {p["id"]: p["title"] for p in included}
        if excluded:
            ctab.delete(kb.build_filter(paper_ids=excluded))
        todo = [p for p in included if p["index_status"] != "ok"]
        log(f"bge: chunking {len(todo)} papers")
        n_chunks = 0
        for i in range(0, len(todo), 25):
            batch = todo[i:i + 25]
            chunk_rows = [r for p in batch for r in _paper_chunks(project, p, rqs.get(p["id"], []), ccfg)]
            ctab.delete(kb.build_filter(paper_ids=[p["id"] for p in batch]))
            if chunk_rows:
                ctab.add(_embed_chunks(chunk_rows, cfg, titles))
            conn.executemany("UPDATE papers SET index_status='ok' WHERE id=?", [(p["id"],) for p in batch])
            conn.commit()
            n_chunks += len(chunk_rows)
            log(f"  {min(i + 25, len(todo))}/{len(todo)} papers, {n_chunks} chunks")

        # refresh RQ tags on already-indexed chunks whose paper's RQ assignment changed
        indexed = {p["id"] for p in included} - {p["id"] for p in todo}
        if indexed:
            cur: dict[str, list] = {}
            n = ctab.count_rows()
            if n:
                for r in ctab.search().where("kind != 'undermind_report'").select(["paper_id", "rq_ids"]) \
                        .limit(n).to_list():
                    cur.setdefault(r["paper_id"], r["rq_ids"])
            for pid in indexed:
                want = rqs.get(pid, [])
                if pid in cur and sorted(cur[pid] or []) != sorted(want):
                    ctab.update(where=f"paper_id = {kb.q(pid)}", values={"rq_ids": want})

        # Undermind reports are small: re-index them every run
        ctab.delete("kind = 'undermind_report'")
        rep_rows = _report_chunks(project, ccfg)
        if rep_rows:
            ctab.add(_embed_chunks(rep_rows, cfg, {}))
        if ctab.count_rows():
            ctab.create_index("text", config=FTS(stem=True, remove_stop_words=True), replace=True)
    total = ctab.count_rows()
    log(f"Done: {len(rows)} papers, {total} chunks ({len(rep_rows)} from Undermind reports)")
    return {"papers": len(rows), "chunks": total, "new_chunks": n_chunks, "report_chunks": len(rep_rows)}
