"""SQLite access for a project's paper metadata."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

PAPER_COLUMNS = ("id", "doi", "openalex_id", "s2_id", "arxiv_id", "pmid", "title", "abstract", "year", "venue",
                 "authors_json", "cited_by_count", "is_oa", "oa_url", "landing_url", "referenced_works_json",
                 "included")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_paper(conn: sqlite3.Connection, paper: dict[str, Any]) -> None:
    """Insert or update metadata columns; pipeline status columns are left untouched on update."""
    cols = [c for c in PAPER_COLUMNS if c in paper]
    sets = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    conn.execute(
        f"INSERT INTO papers ({', '.join(cols)}, updated_at) VALUES ({', '.join('?' * len(cols))}, "
        f"CURRENT_TIMESTAMP) ON CONFLICT(id) DO UPDATE SET {sets}, updated_at=CURRENT_TIMESTAMP",
        [paper[c] for c in cols])


def update_paper(conn: sqlite3.Connection, paper_id: str, **fields: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE papers SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 [*fields.values(), paper_id])


def papers(conn: sqlite3.Connection, where: str = "included=1", params: Iterable = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(f"SELECT * FROM papers WHERE {where} ORDER BY id", tuple(params))]


def rq_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """paper_id -> sorted list of RQ ids."""
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT paper_id, rq_id FROM paper_rq ORDER BY rq_id"):
        out.setdefault(r["paper_id"], []).append(r["rq_id"])
    return out


def authors(paper: dict) -> list[str]:
    try:
        return json.loads(paper.get("authors_json") or "[]")
    except json.JSONDecodeError:
        return []


def short_citation(paper: dict) -> str:
    """'Smith et al., 2020' style label."""
    names = authors(paper)
    last = (names[0].split()[-1] if names and names[0].split() else "Anon.")
    who = last if len(names) <= 1 else (f"{last} & {names[1].split()[-1]}" if len(names) == 2
                                         else f"{last} et al.")
    return f"{who}, {paper.get('year') or 'n.d.'}"
