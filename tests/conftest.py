"""Shared fixtures: isolated project dirs, a fake LLM, deterministic hashing embedders, a tiny PDF builder."""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np
import pytest

from litkb import embed
from litkb.project import create_project


class HashEmbedder:
    """Bag-of-words feature hashing: deterministic, and texts sharing words land close together."""

    def __init__(self, dim: int = 32):
        self.dim = dim

    def embed(self, texts):
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = np.linalg.norm(out[i])
            out[i] = out[i] / n if n else out[i]
        return out


class FakeLLM:
    """Replies by matching prompt content; records every call."""

    model = "fake"

    def __init__(self, replies=None):
        self.replies = replies or []   # list of (substring, reply) or callables(prompt)->reply|None
        self.calls: list[str] = []

    def _reply(self, messages, system=None):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        for rule in self.replies:
            if callable(rule):
                r = rule(prompt)
                if r is not None:
                    return r
            elif rule[0] in prompt:
                return rule[1]
        ids = re.findall(r"id=((?:W\d+|h[0-9a-f]{10}))", prompt)
        cite = f" [{ids[0]}]" if ids else ""
        return f"Synthesised text{cite}."

    def complete(self, messages, *, system=None, max_tokens=4096, temperature=0.2):
        return self._reply(messages, system)

    def stream(self, messages, *, system=None, max_tokens=4096, temperature=0.2):
        yield from self._reply(messages, system).split(" ")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LITKB_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("LITKB_PDF_CACHE_DIR", str(tmp_path / "pdfs"))
    for k in ("UNPAYWALL_EMAIL", "OPENALEX_EMAIL", "SEMANTIC_SCHOLAR_API_KEY", "EZPROXY_PREFIX"):
        monkeypatch.delenv(k, raising=False)
    embed.set_embedders(HashEmbedder(32), HashEmbedder(24))
    yield
    embed.set_embedders()


@pytest.fixture
def project():
    p = create_project("demo")
    (p.root / "config.toml").write_text(
        "[retrieve]\nper_host_delay_s = 0\nmin_pdf_bytes = 200\nbrowser_enabled = false\n"
        "[map]\nhdbscan_min_cluster_size = 3\n", encoding="utf-8")
    p.concept.write_text("# Concept\nHow do sleep and memory consolidation interact in adults?",
                         encoding="utf-8")
    return p


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    import litkb.query
    import litkb.stages.mapping
    import litkb.stages.report
    import litkb.stages.rq
    for mod in (litkb.stages.rq, litkb.stages.mapping, litkb.stages.report, litkb.query):
        monkeypatch.setattr(mod, "get_provider", lambda role, cfg: llm)
    return llm


def make_pdf(text: str, pad: int = 0) -> bytes:
    """Minimal one-page PDF whose text layer is `text` (extractable by pypdf)."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 10 Tf 40 800 Td ({safe}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n%" + b"%" * pad + b"\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def seed_papers(project, papers: list[dict], rq_links: dict[str, list[str]] | None = None) -> None:
    """Insert papers (+ RQs) straight into SQLite."""
    from litkb.db import upsert_paper
    with project.connect() as conn:
        for rq in sorted({r for v in (rq_links or {}).values() for r in v}):
            conn.execute("INSERT OR IGNORE INTO rqs (id, text) VALUES (?, ?)", (rq, f"Question {rq}"))
        for p in papers:
            upsert_paper(conn, {"authors_json": json.dumps(["Ada Lovelace", "Alan Turing"]), "included": 1, **p})
        for pid, rqs in (rq_links or {}).items():
            for i, rq in enumerate(rqs):
                conn.execute("INSERT OR IGNORE INTO paper_rq VALUES (?,?,?,?)", (pid, rq, i + 1, 0.9))
