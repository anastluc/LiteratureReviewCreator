"""Resolver: Semantic Scholar Graph API (`openAccessPdf`). API key optional."""
from __future__ import annotations

from urllib.parse import quote

from ...config import env
from ...http import PoliteClient

API = "https://api.semanticscholar.org/graph/v1/paper/"


class SemanticScholarResolver:
    name = "semantic_scholar"

    def __init__(self, client: PoliteClient):
        self.client = client
        key = env("SEMANTIC_SCHOLAR_API_KEY")
        self.headers = {"x-api-key": key} if key else {}

    async def find_pdf_urls(self, paper: dict) -> list[str]:
        if paper.get("doi"):
            ref = f"DOI:{paper['doi']}"
        elif paper.get("arxiv_id"):
            ref = f"ARXIV:{paper['arxiv_id']}"
        elif paper.get("pmid"):
            ref = f"PMID:{paper['pmid']}"
        else:
            return []
        r = await self.client.get(API + quote(ref, safe=":/"), params={"fields": "openAccessPdf,externalIds"},
                                  headers=self.headers, raise_for_status=False)
        if r.status_code != 200:
            return []
        data = r.json()
        url = (data.get("openAccessPdf") or {}).get("url")
        if aid := (data.get("externalIds") or {}).get("ArXiv"):
            paper.setdefault("arxiv_id", aid)
        return [url] if url else []
