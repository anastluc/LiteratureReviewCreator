"""Resolvers: arXiv (by id, else title search) and bioRxiv / medRxiv (by 10.1101 DOI)."""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from rapidfuzz import fuzz

from ...http import PoliteClient
from ...util import normalize_title

ATOM = "{http://www.w3.org/2005/Atom}"


class ArxivResolver:
    name = "arxiv"

    def __init__(self, client: PoliteClient, title_threshold: int = 93):
        self.client = client
        self.threshold = title_threshold

    async def find_pdf_urls(self, paper: dict) -> list[str]:
        if paper.get("arxiv_id"):
            return [f"https://arxiv.org/pdf/{paper['arxiv_id']}"]
        title = normalize_title(paper.get("title"))
        if not title:
            return []
        r = await self.client.get("https://export.arxiv.org/api/query", raise_for_status=False,
                                  params={"search_query": f'ti:"{title}"', "max_results": 5})
        if r.status_code != 200:
            return []
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            return []
        for entry in root.iter(f"{ATOM}entry"):
            t = normalize_title(entry.findtext(f"{ATOM}title"))
            if fuzz.token_sort_ratio(title, t) >= self.threshold:
                aid = (entry.findtext(f"{ATOM}id") or "").rsplit("/abs/", 1)[-1]
                aid = re.sub(r"v\d+$", "", aid)
                if aid:
                    paper["arxiv_id"] = aid
                    return [f"https://arxiv.org/pdf/{aid}"]
        return []


class BiorxivResolver:
    """bioRxiv and medRxiv share one API; both live under DOI prefix 10.1101."""
    name = "biorxiv"

    def __init__(self, client: PoliteClient):
        self.client = client

    async def find_pdf_urls(self, paper: dict) -> list[str]:
        doi = paper.get("doi") or ""
        if not doi.startswith("10.1101/"):
            return []
        urls = []
        for server in ("biorxiv", "medrxiv"):
            r = await self.client.get(f"https://api.biorxiv.org/details/{server}/{doi}", raise_for_status=False)
            if r.status_code != 200:
                continue
            coll = r.json().get("collection") or []
            if coll:
                v = coll[-1].get("version", 1)
                urls.append(f"https://www.{server}.org/content/{doi}v{v}.full.pdf")
                break
        return urls
