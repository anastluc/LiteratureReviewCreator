"""PDF validity checks: magic bytes, size, and a title sanity check against the first pages' text."""
from __future__ import annotations

import io
import logging
import re

from rapidfuzz import fuzz

from ...util import normalize_title

logging.getLogger("pypdf").setLevel(logging.ERROR)


def first_pages_text(data: bytes, pages: int = 2) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return " ".join((reader.pages[i].extract_text() or "") for i in range(min(pages, len(reader.pages))))
    except Exception:
        return ""


def looks_like_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def check_pdf(data: bytes, title: str | None, *, min_bytes: int = 10_000, threshold: int = 60) -> tuple[bool, str]:
    """(ok, reason). Scanned PDFs without a text layer pass the title check."""
    if not looks_like_pdf(data):
        return False, "not_pdf"
    if len(data) < min_bytes:
        return False, "too_small"
    if title:
        text = normalize_title(first_pages_text(data))
        if len(text) >= 200:
            score = fuzz.partial_ratio(normalize_title(title), text[:8000])
            if score < threshold:
                return False, f"title_mismatch({score:.0f})"
    return True, "ok"


_META_PDF = re.compile(r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)', re.IGNORECASE)
_META_PDF2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']', re.IGNORECASE)


def citation_pdf_url(html: str) -> str | None:
    """Publisher landing pages (Highwire tags) often advertise the PDF in <meta name=citation_pdf_url>."""
    m = _META_PDF.search(html) or _META_PDF2.search(html)
    return m.group(1).replace("&amp;", "&") if m else None
