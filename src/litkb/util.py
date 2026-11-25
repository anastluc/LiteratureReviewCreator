"""Small shared helpers: identifiers, normalisation, JSON/LLM output parsing."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_doi(value: str | None) -> str | None:
    """Return a bare lowercase DOI ('10.x/y') from a DOI, doi.org URL or free text; None if absent."""
    if not value or not isinstance(value, str):
        return None
    m = _DOI_RE.search(value.strip())
    if not m:
        return None
    return m.group(0).rstrip(".,;)]}").lower()


def normalize_title(title: str | None) -> str:
    """Lowercase, accent-stripped, punctuation-free title used for fuzzy matching."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"<[^>]+>", " ", t)              # inline markup like <i>
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def openalex_short_id(value: str | None) -> str | None:
    """'https://openalex.org/W123' -> 'W123'."""
    if not value:
        return None
    m = re.search(r"(W\d+)", value)
    return m.group(1) if m else None


def fallback_paper_id(doi: str | None, title: str | None) -> str:
    """Id for papers OpenAlex could not match (decision: OpenAlex W-id, else 'h' + sha1[:10])."""
    key = doi or normalize_title(title)
    return "h" + sha1(key)[:10]


def pdf_cache_key(doi: str | None, paper_id: str) -> str:
    """Shared PDF cache file stem: sha1(doi), or sha1(paper id) when no DOI."""
    return sha1(doi or paper_id)


def parse_json_block(text: str):
    """Parse JSON from an LLM reply, tolerating ```json fences and leading prose."""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        end = max(text.rfind("}"), text.rfind("]"))
        return json.loads(text[start:end + 1])


def approx_tokens(text: str) -> int:
    """Cheap token estimate (~1.3 tokens per word for English academic text)."""
    return int(len(text.split()) * 1.3) + 1


def reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """OpenAlex abstract_inverted_index -> plain text."""
    if not inverted_index:
        return None
    pos: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos)) or None
