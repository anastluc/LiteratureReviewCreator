"""OpenAlex metadata client (used by merge for enrichment and by retrieve as an OA resolver)."""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from rapidfuzz import fuzz

from .config import env
from .http import PoliteClient
from .util import normalize_doi, normalize_title, openalex_short_id, reconstruct_abstract

API = "https://api.openalex.org/works"
SELECT = ("id,doi,display_name,publication_year,primary_location,authorships,cited_by_count,open_access,"
          "abstract_inverted_index,referenced_works,ids,best_oa_location,locations")


def _params(**extra) -> dict:
    p = {"select": SELECT, **extra}
    if mail := env("OPENALEX_EMAIL") or env("UNPAYWALL_EMAIL"):
        p["mailto"] = mail
    return p


def arxiv_id_from(work: dict) -> str | None:
    doi = normalize_doi(work.get("doi")) or ""
    if m := re.match(r"10\.48550/arxiv\.(.+)", doi):
        return m.group(1)
    for loc in work.get("locations") or []:
        url = (loc or {}).get("landing_page_url") or ""
        if m := re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", url):
            return m.group(1)
    return None


def work_to_paper(work: dict) -> dict:
    """OpenAlex work -> papers-table fields."""
    loc = work.get("primary_location") or {}
    src = loc.get("source") or {}
    oa = work.get("open_access") or {}
    ids = work.get("ids") or {}
    pmid = ids.get("pmid")
    return {
        "id": openalex_short_id(work.get("id")),
        "openalex_id": openalex_short_id(work.get("id")),
        "doi": normalize_doi(work.get("doi")),
        "title": work.get("display_name") or work.get("title"),
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "year": work.get("publication_year"),
        "venue": src.get("display_name"),
        "authors_json": json.dumps([(a.get("author") or {}).get("display_name") for a in
                                    work.get("authorships") or [] if (a.get("author") or {}).get("display_name")]),
        "cited_by_count": work.get("cited_by_count"),
        "is_oa": int(bool(oa.get("is_oa"))),
        "oa_url": oa.get("oa_url"),
        "landing_url": loc.get("landing_page_url"),
        "referenced_works_json": json.dumps([openalex_short_id(w) for w in work.get("referenced_works") or []]),
        "arxiv_id": arxiv_id_from(work),
        "pmid": pmid.rsplit("/", 1)[-1] if pmid else None,
    }


def pdf_urls(work: dict) -> list[str]:
    """Candidate PDF URLs from an OpenAlex work, best first."""
    urls = []
    for loc in [work.get("best_oa_location") or {}, *(work.get("locations") or [])]:
        if loc and loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
    if (work.get("open_access") or {}).get("oa_url"):
        urls.append(work["open_access"]["oa_url"])
    return list(dict.fromkeys(urls))


class OpenAlex:
    def __init__(self, client: PoliteClient):
        self.client = client

    async def by_dois(self, dois: list[str]) -> dict[str, dict]:
        """DOI -> work, for up to 50 DOIs per request."""
        out: dict[str, dict] = {}
        if not dois:
            return out
        data = await self.client.get_json(API, params=_params(
            filter="doi:" + "|".join(dois), **{"per-page": 50}))
        for w in data.get("results", []):
            if d := normalize_doi(w.get("doi")):
                out[d] = w
        return out

    async def by_id(self, work_id: str) -> dict | None:
        r = await self.client.get(f"{API}/{quote(work_id)}", params=_params(), raise_for_status=False)
        return r.json() if r.status_code == 200 else None

    async def by_title(self, title: str, year: int | None = None, threshold: int = 93) -> dict | None:
        """Best fuzzy title match (and year within ±1, when known)."""
        q = normalize_title(title)
        if not q:
            return None
        data = await self.client.get_json(API, params=_params(search=q, **{"per-page": 5}))
        best, best_score = None, 0.0
        for w in data.get("results", []):
            score = fuzz.token_sort_ratio(q, normalize_title(w.get("display_name")))
            if year and w.get("publication_year") and abs(int(year) - w["publication_year"]) > 1:
                continue
            if score > best_score:
                best, best_score = w, score
        return best if best_score >= threshold else None
