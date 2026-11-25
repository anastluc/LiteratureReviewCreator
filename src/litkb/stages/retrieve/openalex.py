"""Resolver: OpenAlex (`open_access.oa_url`, `best_oa_location`, `locations[].pdf_url`)."""
from __future__ import annotations

from ...http import PoliteClient
from ...openalex import OpenAlex, pdf_urls


class OpenAlexResolver:
    name = "openalex"

    def __init__(self, client: PoliteClient):
        self.oa = OpenAlex(client)

    async def find_pdf_urls(self, paper: dict) -> list[str]:
        work = None
        if paper.get("openalex_id"):
            work = await self.oa.by_id(paper["openalex_id"])
        elif paper.get("doi"):
            work = (await self.oa.by_dois([paper["doi"]])).get(paper["doi"])
        urls = pdf_urls(work) if work else []
        if paper.get("oa_url") and paper["oa_url"] not in urls:
            urls.append(paper["oa_url"])
        return urls
