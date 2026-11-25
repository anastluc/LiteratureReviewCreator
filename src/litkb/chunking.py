"""Section-aware chunking (D11) of GROBID JSON, abstracts and Undermind reports."""
from __future__ import annotations

import re

from .util import approx_tokens

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


def _split_long(text: str, target: int, overlap: int) -> list[str]:
    """Split one long text into ~target-token windows on sentence boundaries, with overlap."""
    sents = _SENT.split(text) or [text]
    out, cur, cur_tok = [], [], 0
    for s in sents:
        t = approx_tokens(s)
        if t > target:  # a monster "sentence" (tables, lists): hard word split
            words = s.split()
            step = max(1, int(target / 1.3))
            for i in range(0, len(words), step):
                sents_piece = " ".join(words[i:i + step])
                if cur:
                    out.append(" ".join(cur))
                    cur, cur_tok = [], 0
                out.append(sents_piece)
            continue
        if cur and cur_tok + t > target:
            out.append(" ".join(cur))
            tail, tail_tok = [], 0
            for prev in reversed(cur):
                tail_tok += approx_tokens(prev)
                if tail_tok > overlap:
                    break
                tail.insert(0, prev)
            cur, cur_tok = tail, sum(approx_tokens(x) for x in tail)
        cur.append(s)
        cur_tok += t
    if cur:
        out.append(" ".join(cur))
    return out


def chunk_paragraphs(paragraphs: list[dict], target: int, overlap: int, min_tokens: int) -> list[dict]:
    """Merge short paragraphs / split long ones. Each paragraph is {text, page}. Returns [{text, page}]."""
    chunks: list[dict] = []
    cur: list[str] = []
    cur_tok, cur_page = 0, None
    for p in paragraphs:
        text, page = p["text"].strip(), p.get("page")
        if not text:
            continue
        t = approx_tokens(text)
        if t > target:
            if cur:
                chunks.append({"text": "\n".join(cur), "page": cur_page})
                cur, cur_tok, cur_page = [], 0, None
            chunks.extend({"text": piece, "page": page} for piece in _split_long(text, target, overlap))
            continue
        if cur and cur_tok + t > target:
            chunks.append({"text": "\n".join(cur), "page": cur_page})
            # overlap: carry the tail of the previous chunk
            prev = " ".join(cur).split()
            keep = int(overlap / 1.3)
            cur = [" ".join(prev[-keep:])] if keep and len(prev) > keep else []
            cur_tok, cur_page = sum(approx_tokens(x) for x in cur), None
        cur.append(text)
        cur_tok += t
        cur_page = cur_page if cur_page is not None else page
    if cur:
        chunks.append({"text": "\n".join(cur), "page": cur_page})
    # fold tiny trailing chunks into their predecessor
    merged: list[dict] = []
    for c in chunks:
        if merged and approx_tokens(c["text"]) < min_tokens:
            merged[-1]["text"] += "\n" + c["text"]
        else:
            merged.append(c)
    return merged


def _dropped(section: dict, drop: set[str]) -> bool:
    title = re.sub(r"^[\dIVX.\s]+", "", (section.get("title") or "")).strip().lower()
    return title in drop or (section.get("type") or "").lower() in drop or \
        any(title.startswith(d) for d in drop)


def chunk_document(doc: dict, ccfg: dict) -> list[dict]:
    """GROBID JSON -> [{section, page, text}] for full-text chunks."""
    drop = {d.lower() for d in ccfg["drop_sections"]}
    out = []
    for sec in doc.get("sections", []):
        if _dropped(sec, drop):
            continue
        for c in chunk_paragraphs(sec["paragraphs"], ccfg["target_tokens"], ccfg["overlap_tokens"],
                                  ccfg["min_tokens"]):
            out.append({"section": sec["title"], "page": c["page"], "text": c["text"]})
    return out


def chunk_text(text: str, ccfg: dict) -> list[str]:
    paras = [{"text": p, "page": None} for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [c["text"] for c in chunk_paragraphs(paras, ccfg["target_tokens"], ccfg["overlap_tokens"],
                                                ccfg["min_tokens"])]


def chunk_markdown(md: str, ccfg: dict) -> list[dict]:
    """Split a Markdown report by headings, then chunk each part. Returns [{section, text}]."""
    parts: list[tuple[str, list[str]]] = [("Introduction", [])]
    for line in md.splitlines():
        if m := re.match(r"^#{1,6}\s+(.+)", line):
            parts.append((m.group(1).strip(), []))
        else:
            parts[-1][1].append(line)
    out = []
    for heading, lines in parts:
        body = "\n".join(lines).strip()
        if body:
            out.extend({"section": heading, "text": t} for t in chunk_text(body, ccfg))
    return out
