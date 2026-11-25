"""Stage 5: parse PDFs with GROBID (D7) -> parsed/<id>.tei.xml + parsed/<id>.json.

The JSON is {title, abstract, sections: [{title, paragraphs: [{text, page}]}], references, figures}.
If GROBID fails the paper is marked parse_status = failed and indexing falls back to its abstract.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path

from lxml import etree

from ..db import papers as db_papers
from ..db import update_paper
from ..http import PoliteClient
from ..project import Project
from ..util import normalize_doi

TEI = {"t": "http://www.tei-c.org/ns/1.0"}
Log = Callable[[str], None]


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _page(el) -> int | None:
    coords = el.get("coords") or ""
    m = re.match(r"\s*(\d+),", coords)
    if m:
        return int(m.group(1))
    for sub in el.iter():
        if sub.get("coords"):
            m = re.match(r"\s*(\d+),", sub.get("coords"))
            if m:
                return int(m.group(1))
    return None


def _div_sections(divs, kind: str) -> list[dict]:
    out = []
    for div in divs:
        head = div.find("t:head", TEI)
        title = _text(head) or (div.get("type") or "").replace("_", " ").title() or "Untitled section"
        last_page = _page(head) if head is not None else None
        paras = []
        for el in div:
            tag = etree.QName(el).localname
            if tag not in ("p", "formula", "list"):
                continue
            txt = _text(el)
            if not txt:
                continue
            last_page = _page(el) or last_page
            paras.append({"text": txt, "page": last_page})
        if paras:
            out.append({"title": title, "kind": kind, "type": div.get("type"), "paragraphs": paras})
    return out


def parse_tei(xml: bytes | str) -> dict:
    root = etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml,
                            parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))
    title = _text(root.find(".//t:teiHeader//t:titleStmt/t:title", TEI))
    abstract = " ".join(_text(p) for p in root.findall(".//t:profileDesc/t:abstract//t:p", TEI)) or \
        _text(root.find(".//t:profileDesc/t:abstract", TEI))
    sections = _div_sections(root.findall(".//t:text/t:body/t:div", TEI), "body")
    sections += _div_sections(root.findall(".//t:text/t:back/t:div", TEI), "back")
    sections += _div_sections(root.findall(".//t:text/t:back/t:div/t:div", TEI), "back")
    refs = []
    for b in root.findall(".//t:listBibl/t:biblStruct", TEI):
        t = b.find("t:analytic/t:title", TEI)
        if t is None:
            t = b.find("t:monogr/t:title", TEI)
        doi = b.find(".//t:idno[@type='DOI']", TEI)
        date = b.find(".//t:imprint/t:date", TEI)
        year = re.match(r"(\d{4})", (date.get("when") or "")) if date is not None else None
        refs.append({"id": b.get("{http://www.w3.org/XML/1998/namespace}id"), "title": _text(t) or None,
                     "doi": normalize_doi(_text(doi)) if doi is not None else None,
                     "year": int(year.group(1)) if year else None})
    figures = []
    for f in root.findall(".//t:text//t:figure", TEI):
        cap = " ".join(x for x in (_text(f.find("t:head", TEI)), _text(f.find("t:figDesc", TEI))) if x)
        if cap:
            figures.append({"type": f.get("type") or "figure", "caption": cap, "page": _page(f)})
    return {"title": title, "abstract": abstract, "sections": sections, "references": refs, "figures": figures}


async def grobid_alive(client: PoliteClient, url: str) -> bool:
    try:
        r = await client.get(f"{url}/api/isalive", raise_for_status=False)
        return r.status_code == 200
    except Exception:
        return False


async def _parse_all(project: Project, todo: list[dict], log: Log, transport=None) -> dict:
    pcfg = project.config["parse"]
    url = pcfg["grobid_url"].rstrip("/")
    counts = {"ok": 0, "failed": 0}
    async with PoliteClient(delay_s=0, timeout_s=pcfg["timeout_s"], max_attempts=2, transport=transport) as c:
        if not await grobid_alive(c, url):
            raise RuntimeError(f"GROBID is not reachable at {url}. Start Docker Desktop, then "
                               "`docker compose up -d grobid` (first start takes ~1 min).")
        sem = asyncio.Semaphore(pcfg["concurrency"])
        with project.connect() as conn:
            async def one(p):
                async with sem:
                    pdf = Path(p["pdf_path"])
                    try:
                        r = await c.post(f"{url}/api/processFulltextDocument",
                                         files={"input": (pdf.name, pdf.read_bytes(), "application/pdf")},
                                         data={"consolidateHeader": "0", "consolidateCitations": "0",
                                               "segmentSentences": "0",
                                               "teiCoordinates": ["p", "head", "figure"]})
                        tei = r.content
                        doc = parse_tei(tei)
                        (project.parsed_dir / f"{p['id']}.tei.xml").write_bytes(tei)
                        (project.parsed_dir / f"{p['id']}.json").write_text(
                            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
                        update_paper(conn, p["id"], parse_status="ok", index_status="pending")
                        counts["ok"] += 1
                        log(f"  ok  {p['id']} ({len(doc['sections'])} sections)")
                    except Exception as e:
                        update_paper(conn, p["id"], parse_status="failed")
                        counts["failed"] += 1
                        log(f"  --  {p['id']} parse failed: {type(e).__name__}: {str(e)[:120]}")
                    conn.commit()
            await asyncio.gather(*(one(p) for p in todo))
    return counts


def run(project: Project, *, retry: bool = False, log: Log = print, transport=None) -> dict:
    project.ensure_dirs()
    statuses = ("pending", "failed") if retry else ("pending",)
    with project.connect() as conn:
        todo = [p for p in db_papers(conn, f"included=1 AND pdf_status='ok' AND parse_status IN "
                                           f"({','.join('?' * len(statuses))})", statuses)
                if p["pdf_path"] and Path(p["pdf_path"]).exists()]
    log(f"Parsing {len(todo)} PDFs with GROBID")
    counts = asyncio.run(_parse_all(project, todo, log, transport)) if todo else {"ok": 0, "failed": 0}
    log(f"Done: {counts['ok']} parsed, {counts['failed']} failed (failed papers are indexed by abstract)")
    return counts
