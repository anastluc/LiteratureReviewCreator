"""LanceDB knowledge base (D9): tables `papers` (SPECTER2) and `chunks` (bge + FTS), hybrid search (§5.6)."""
from __future__ import annotations

from collections.abc import Iterable

import lancedb
import pyarrow as pa

from .embed import chunk_embedder, query_vector
from .project import Project

PAPERS, CHUNKS = "papers", "chunks"


def papers_schema(dim: int) -> pa.Schema:
    return pa.schema([
        ("paper_id", pa.string()), ("vector", pa.list_(pa.float32(), dim)), ("title", pa.string()),
        ("abstract", pa.string()), ("year", pa.int32()), ("rq_ids", pa.list_(pa.string())),
        ("cluster_id", pa.int32()),
    ])


def chunks_schema(dim: int) -> pa.Schema:
    return pa.schema([
        ("chunk_id", pa.string()), ("paper_id", pa.string()), ("kind", pa.string()), ("section", pa.string()),
        ("page", pa.int32()), ("text", pa.string()), ("vector", pa.list_(pa.float32(), dim)),
        ("rq_ids", pa.list_(pa.string())), ("year", pa.int32()),
    ])


def connect(project: Project):
    return lancedb.connect(str(project.index_dir))


def table_names(db) -> list[str]:
    names = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
    names = getattr(names, "tables", names)
    return list(names)


def open_table(db, name: str):
    return db.open_table(name) if name in table_names(db) else None


def ensure_table(db, name: str, schema: pa.Schema):
    t = open_table(db, name)
    if t is not None and t.schema.field("vector").type != schema.field("vector").type:
        raise ValueError(f"LanceDB table '{name}' has a different vector size; rerun `lit index --rebuild`")
    return t if t is not None else db.create_table(name, schema=schema)


def q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_filter(paper_ids: Iterable[str] | None = None, rq_ids: Iterable[str] | None = None,
                 year_min: int | None = None, year_max: int | None = None,
                 kinds: Iterable[str] | None = None) -> str | None:
    parts = []
    if paper_ids:
        parts.append(f"paper_id IN ({', '.join(q(p) for p in paper_ids)})")
    if rq_ids:
        parts.append(f"array_has_any(rq_ids, [{', '.join(q(r) for r in rq_ids)}])")
    if year_min:
        parts.append(f"year >= {int(year_min)}")
    if year_max:
        parts.append(f"year <= {int(year_max)}")
    if kinds:
        parts.append(f"kind IN ({', '.join(q(k) for k in kinds)})")
    return " AND ".join(parts) or None


def rrf(result_lists: list[list[dict]], key: str = "chunk_id", k: int = 60) -> list[dict]:
    """Reciprocal-rank fusion of several ranked lists."""
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for results in result_lists:
        for rank, r in enumerate(results):
            scores[r[key]] = scores.get(r[key], 0.0) + 1.0 / (k + rank + 1)
            rows.setdefault(r[key], r)
    fused = sorted(rows.values(), key=lambda r: -scores[r[key]])
    for r in fused:
        r["_rrf"] = scores[r[key]]
    return fused


def search_chunks(project: Project, question: str, *, k: int | None = None, paper_ids=None, rq_ids=None,
                  year_min=None, year_max=None, kinds=None, hybrid: bool | None = None) -> list[dict]:
    cfg = project.config
    k = k or cfg["query"]["top_k"]
    hybrid = cfg["query"]["hybrid"] if hybrid is None else hybrid
    t = open_table(connect(project), CHUNKS)
    if t is None:
        raise FileNotFoundError("No chunks index yet; run `lit index` first")
    where = build_filter(paper_ids, rq_ids, year_min, year_max, kinds)
    pool = max(k * 4, 40)
    vec = query_vector(chunk_embedder(cfg), question)
    vq = t.search(vec, vector_column_name="vector")
    if where:
        vq = vq.where(where, prefilter=True)
    lists = [vq.limit(pool).to_list()]
    if hybrid:
        try:
            fq = t.search(question, query_type="fts")
            if where:
                fq = fq.where(where, prefilter=True)
            lists.append(fq.limit(pool).to_list())
        except Exception:  # FTS index missing or query syntax the tokenizer rejects
            pass
    out = rrf(lists, k=cfg["query"]["rrf_k"])[:k]
    for r in out:
        r.pop("vector", None)
    return out


def all_papers(project: Project) -> list[dict]:
    t = open_table(connect(project), PAPERS)
    return t.to_arrow().to_pylist() if t is not None else []
