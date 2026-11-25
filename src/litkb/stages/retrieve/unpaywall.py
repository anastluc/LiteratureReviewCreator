"""Resolver: Unpaywall (`best_oa_location.url_for_pdf`, then other OA locations)."""
from __future__ import annotations

from urllib.parse import quote

from ...config import env
from ...http import PoliteClient


class UnpaywallResolver:
    name = "unpaywall"

    def __init__(self, client: PoliteClient):
        self.client = client
        self.email = env("UNPAYWALL_EMAIL")

    async def find_pdf_urls(self, paper: dict) -> list[str]:
        if not paper.get("doi") or not self.email:
            return []
        r = await self.client.get(f"https://api.unpaywall.org/v2/{quote(paper['doi'], safe='/')}",
                                  params={"email": self.email}, raise_for_status=False)
        if r.status_code != 200:
            return []
        data = r.json()
        locs = [data.get("best_oa_location") or {}, *(data.get("oa_locations") or [])]
        urls = [loc["url_for_pdf"] for loc in locs if loc and loc.get("url_for_pdf")]
        urls += [loc["url"] for loc in locs if loc and loc.get("url") and loc.get("url") not in urls]
        return list(dict.fromkeys(urls))
