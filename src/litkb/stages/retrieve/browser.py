"""Browser fallback (D6): Playwright persistent profile, EZproxy-prefixed DOI links, human-like pacing.

Credentials are never seen by this code: `lit login` opens the same profile non-headless so the user signs in
through their university SSO; afterwards only the profile's session cookies are reused.
"""
from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urljoin

from ...config import env
from .validate import check_pdf, citation_pdf_url

# Publisher-agnostic PDF link heuristics, most specific first.
PDF_LINK_SELECTORS = [
    "a[href*='/doi/pdf/']", "a[href*='/doi/epdf/']", "a[href*='pdfdirect']", "a[href*='/pdf/']",
    "a[href$='.pdf']", "a[href*='.pdf?']", "a[title*='PDF' i]", "a:has-text('Download PDF')",
    "a:has-text('View PDF')", "a:has-text('PDF')",
]


def proxied(url: str) -> str:
    prefix = env("EZPROXY_PREFIX") or ""
    return prefix + url if prefix else url


def open_context(profile_dir: Path, headless: bool):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    profile_dir.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=headless, accept_downloads=True,
                                                viewport={"width": 1280, "height": 900})
    return pw, ctx


def login(profile_dir: Path, start_url: str | None = None) -> None:
    """Open the persistent profile visibly; the user signs in, then closes the window."""
    pw, ctx = open_context(profile_dir, headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(start_url or proxied("https://doi.org/10.1038/nature14539"))
        print("Sign in through your university in the browser window, then close the window to finish.")
        ctx.wait_for_event("close", timeout=0)
    except Exception:
        pass  # window closed by the user
    finally:
        try:
            ctx.close()
        finally:
            pw.stop()


def _candidate_urls(page) -> Iterator[str]:
    try:
        if u := citation_pdf_url(page.content()):
            yield u
    except Exception:
        pass
    for sel in PDF_LINK_SELECTORS:
        try:
            for el in page.locator(sel).all()[:3]:
                href = el.get_attribute("href")
                if href:
                    yield urljoin(page.url, href)
        except Exception:
            continue


class BrowserFetcher:
    def __init__(self, profile_dir: Path, *, headless: bool, delay_s: float, max_per_hour: int,
                 min_bytes: int, title_threshold: int, log: Callable[[str], None] = print):
        self.profile_dir, self.headless = profile_dir, headless
        self.delay_s, self.max_per_hour = delay_s, max_per_hour
        self.min_bytes, self.title_threshold = min_bytes, title_threshold
        self.log = log
        self._times: deque[float] = deque()

    def __enter__(self) -> BrowserFetcher:
        self.pw, self.ctx = open_context(self.profile_dir, self.headless)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.ctx.close()
        finally:
            self.pw.stop()

    def _throttle(self) -> None:
        now = time.monotonic()
        while self._times and now - self._times[0] > 3600:
            self._times.popleft()
        if len(self._times) >= self.max_per_hour:
            wait = 3600 - (now - self._times[0]) + 1
            self.log(f"  browser: hourly cap ({self.max_per_hour}) reached, waiting {wait / 60:.0f} min")
            time.sleep(wait)
        if self._times:
            gap = self.delay_s * random.uniform(0.7, 1.5) - (time.monotonic() - self._times[-1])
            if gap > 0:
                time.sleep(gap)
        self._times.append(time.monotonic())

    def _fetch(self, url: str) -> bytes | None:
        try:
            r = self.ctx.request.get(url, timeout=60_000)
            return r.body() if r.ok else None
        except Exception:
            return None

    def fetch(self, paper: dict) -> tuple[bytes | None, str, list[tuple[str, str]]]:
        """Return (pdf_bytes|None, final_url, attempts[(url, outcome)])."""
        attempts: list[tuple[str, str]] = []
        start = f"https://doi.org/{paper['doi']}" if paper.get("doi") else paper.get("landing_url")
        if not start:
            return None, "", [("", "no_doi_or_url")]
        url = proxied(start)
        self._throttle()
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            self.page.wait_for_timeout(int(random.uniform(1500, 4000)))
        except Exception as e:
            return None, url, [(url, f"nav_error:{type(e).__name__}")]
        if resp and "pdf" in (resp.headers.get("content-type") or ""):
            data = self._fetch(self.page.url)
            ok, why = check_pdf(data or b"", paper.get("title"), min_bytes=self.min_bytes,
                                threshold=self.title_threshold)
            if ok:
                return data, self.page.url, [(self.page.url, "ok")]
            attempts.append((self.page.url, why))
        seen = set()
        for cand in _candidate_urls(self.page):
            if cand in seen or len(seen) >= 4:
                continue
            seen.add(cand)
            data = self._fetch(cand)
            ok, why = check_pdf(data or b"", paper.get("title"), min_bytes=self.min_bytes,
                                threshold=self.title_threshold)
            attempts.append((cand, "ok" if ok else (why if data else "http_error")))
            if ok:
                return data, cand, attempts
        if not attempts:
            attempts.append((self.page.url, "no_pdf_link"))
        return None, self.page.url, attempts
