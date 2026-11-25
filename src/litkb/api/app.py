"""FastAPI backend (ARCHITECTURE.md §5.9). Serves the built React app from frontend/dist when present."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import REPO_ROOT
from ..db import authors, rq_map
from ..db import papers as db_papers
from ..project import Project, list_projects

app = FastAPI(title="litkb")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _project(p: str) -> Project:
    if p not in list_projects():
        raise HTTPException(404, f"Unknown project '{p}'")
    return Project.open(p)


def _paper_out(row: dict, rqs: dict[str, list[str]], clusters: dict[str, int] | None = None,
               full: bool = False) -> dict:
    out = {k: row.get(k) for k in ("id", "doi", "title", "year", "venue", "cited_by_count", "is_oa", "included",
                                   "pdf_status", "pdf_source", "parse_status", "index_status", "landing_url")}
    out["authors"] = authors(row)
    out["rq_ids"] = rqs.get(row["id"], [])
    out["cluster_id"] = (clusters or {}).get(row["id"])
    out["has_pdf"] = row.get("pdf_status") == "ok"
    if full:
        out["abstract"] = row.get("abstract")
    return out


def _clusters_by_paper(project: Project) -> dict[str, int]:
    f = project.map_dir / "projection.parquet"
    if not f.exists():
        return {}
    df = pd.read_parquet(f, columns=["paper_id", "cluster_id"])
    return dict(zip(df["paper_id"], df["cluster_id"].astype(int)))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/projects")
def projects() -> list[dict]:
    out = []
    for slug in list_projects():
        p = Project.open(slug)
        info = {"slug": slug, "papers": 0, "has_map": (p.map_dir / "projection.parquet").exists(),
                "reports": sorted(f.stem for f in p.reports_dir.glob("*.md")) if p.reports_dir.exists() else []}
        if p.db_path.exists():
            with p.connect() as conn:
                info["papers"] = conn.execute("SELECT COUNT(*) FROM papers WHERE included=1").fetchone()[0]
                info["rqs"] = [dict(r) for r in conn.execute("SELECT id, text FROM rqs ORDER BY id")]
        out.append(info)
    return out


@app.get("/api/{p}/papers")
def list_papers(p: str, rq: str | None = None, q: str | None = None, year_min: int | None = None,
                year_max: int | None = None, pdf_status: str | None = None,
                include_excluded: bool = False) -> list[dict]:
    project = _project(p)
    where, params = ["1=1" if include_excluded else "included=1"], []
    if year_min:
        where.append("year >= ?"), params.append(year_min)
    if year_max:
        where.append("year <= ?"), params.append(year_max)
    if pdf_status:
        where.append("pdf_status = ?"), params.append(pdf_status)
    if q:
        where.append("(title LIKE ? OR abstract LIKE ?)"), params.extend([f"%{q}%", f"%{q}%"])
    if rq:
        where.append("id IN (SELECT paper_id FROM paper_rq WHERE rq_id = ?)"), params.append(rq)
    with project.connect() as conn:
        rows = db_papers(conn, " AND ".join(where), params)
        rqs = rq_map(conn)
    clusters = _clusters_by_paper(project)
    return [_paper_out(r, rqs, clusters) for r in rows]


@app.get("/api/{p}/papers/{paper_id}")
def get_paper(p: str, paper_id: str) -> dict:
    project = _project(p)
    with project.connect() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown paper")
        rqs = rq_map(conn)
        ranks = [dict(r) for r in conn.execute(
            "SELECT rq_id, undermind_rank, undermind_score FROM paper_rq WHERE paper_id=?", (paper_id,))]
        cites = [r[0] for r in conn.execute("SELECT dst_paper_id FROM citations WHERE src_paper_id=?", (paper_id,))]
        cited_by = [r[0] for r in conn.execute("SELECT src_paper_id FROM citations WHERE dst_paper_id=?",
                                               (paper_id,))]
    out = _paper_out(dict(row), rqs, _clusters_by_paper(project), full=True)
    out.update(undermind=ranks, cites=cites, cited_by=cited_by)
    return out


@app.get("/api/{p}/pdf/{paper_id}")
def get_pdf(p: str, paper_id: str):
    project = _project(p)
    with project.connect() as conn:
        row = conn.execute("SELECT pdf_path, pdf_status FROM papers WHERE id=?", (paper_id,)).fetchone()
    if row is None or row["pdf_status"] != "ok" or not row["pdf_path"] or not Path(row["pdf_path"]).exists():
        raise HTTPException(404, "No PDF for this paper")
    return FileResponse(row["pdf_path"], media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{paper_id}.pdf"'})


@app.get("/api/{p}/map")
def get_map(p: str, method: str = Query("umap", pattern="^(tsne|umap)$")) -> dict:
    project = _project(p)
    f = project.map_dir / "projection.parquet"
    if not f.exists():
        raise HTTPException(404, "No map yet; run `lit map`")
    df = pd.read_parquet(f)
    df = df.astype(object).where(df.notna(), None)
    points = [{"id": r["paper_id"], "x": float(r[f"{method}_x"]), "y": float(r[f"{method}_y"]),
               "cluster_id": int(r["cluster_id"]), "title": r["title"],
               "year": int(r["year"]) if r["year"] is not None else None,
               "rq_ids": [x for x in (r["rq_ids"] or "").split(";") if x],
               "cited_by_count": int(r["cited_by_count"]) if r["cited_by_count"] is not None else None}
              for r in df.to_dict("records")]
    clusters = json.loads((project.map_dir / "clusters.json").read_text(encoding="utf-8")) \
        if (project.map_dir / "clusters.json").exists() else []
    for c in clusters:
        c["centroid"] = c.get(f"centroid_{method}")
    return {"method": method, "points": points, "clusters": clusters}


@app.get("/api/{p}/citations")
def citations(p: str) -> list[dict]:
    project = _project(p)
    with project.connect() as conn:
        return [{"source": r[0], "target": r[1]} for r in
                conn.execute("SELECT src_paper_id, dst_paper_id FROM citations")]


class QueryBody(BaseModel):
    question: str
    paper_ids: list[str] | None = None
    rq_ids: list[str] | None = None
    year_min: int | None = None
    year_max: int | None = None
    stream: bool = True


@app.post("/api/{p}/query")
async def query(p: str, body: QueryBody):
    from ..query import answer, answer_stream
    project = _project(p)
    kw = dict(paper_ids=body.paper_ids, rq_ids=body.rq_ids, year_min=body.year_min, year_max=body.year_max)
    if not body.stream:
        return answer(project, body.question, **kw)

    async def events():
        try:
            async for event, data in iterate_in_threadpool(answer_stream(project, body.question, **kw)):
                yield {"event": event, "data": json.dumps(data)}
        except Exception as e:  # surface errors (missing API key, no index) to the client
            yield {"event": "error", "data": json.dumps({"message": f"{type(e).__name__}: {e}"})}
    return EventSourceResponse(events())


@app.get("/api/{p}/reports")
def reports(p: str) -> list[str]:
    project = _project(p)
    return sorted(f.stem for f in project.reports_dir.glob("*.md")) if project.reports_dir.exists() else []


@app.get("/api/{p}/reports/{name}")
def get_report(p: str, name: str) -> dict:
    project = _project(p)
    f = (project.reports_dir / f"{name}.md").resolve()
    if f.parent != project.reports_dir.resolve() or not f.exists():
        raise HTTPException(404, "Unknown report")
    return {"name": name, "markdown": f.read_text(encoding="utf-8")}


class SPAStaticFiles(StaticFiles):
    """Static files with a single-page-app fallback: unknown non-API paths (e.g. /p/demo/map) get index.html."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            first = path.replace("\\", "/").split("/", 1)[0]
            if e.status_code != 404 or first in ("api", "assets"):
                raise
            return await super().get_response("index.html", scope)


_dist = REPO_ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/", SPAStaticFiles(directory=_dist, html=True), name="frontend")
