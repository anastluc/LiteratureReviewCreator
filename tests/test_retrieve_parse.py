import csv
import json

import httpx
import respx
from conftest import make_pdf, seed_papers

from litkb.stages import parse, retrieve
from litkb.stages.parse import parse_tei
from litkb.stages.retrieve.validate import check_pdf, citation_pdf_url

TITLE_A = "Sleep spindles and memory consolidation"
TITLE_B = "Slow oscillations orchestrate hippocampal replay"


def test_check_pdf():
    pdf = make_pdf(TITLE_A + " " + "lorem ipsum dolor " * 20)
    assert check_pdf(pdf, TITLE_A, min_bytes=100) == (True, "ok")
    ok, why = check_pdf(pdf, "Completely different subject about volcanoes", min_bytes=100)
    assert not ok and why.startswith("title_mismatch")
    assert check_pdf(b"<html>", TITLE_A, min_bytes=1) == (False, "not_pdf")
    assert check_pdf(pdf, TITLE_A, min_bytes=10**7) == (False, "too_small")
    html = '<meta name="citation_pdf_url" content="https://pub.org/a.pdf?x=1&amp;y=2">'
    assert citation_pdf_url(html) == "https://pub.org/a.pdf?x=1&y=2"


@respx.mock
def test_retrieve_chain_inbox_and_missing(project, monkeypatch):
    monkeypatch.setenv("UNPAYWALL_EMAIL", "test@example.org")
    seed_papers(project, [
        {"id": "W1", "doi": "10.1234/a", "title": TITLE_A, "is_oa": 1},
        {"id": "W2", "doi": "10.1234/b", "title": TITLE_B, "is_oa": 1, "openalex_id": "W2"},
        {"id": "W3", "doi": "10.1234/c", "title": "A paywalled study of dreams", "is_oa": 0},
        {"id": "W4", "doi": "10.1234/d", "title": "Manually supplied paper on naps", "is_oa": 0},
    ])
    pdf_a = make_pdf(TITLE_A + " text " * 50)
    pdf_b = make_pdf(TITLE_B + " text " * 50)
    respx.get("https://api.unpaywall.org/v2/10.1234/a").mock(return_value=httpx.Response(200, json={
        "best_oa_location": {"url_for_pdf": "https://repo.org/a.pdf"}, "oa_locations": []}))
    respx.get(url__regex=r"https://api\.unpaywall\.org/v2/.*").mock(return_value=httpx.Response(404))
    respx.get("https://repo.org/a.pdf").mock(return_value=httpx.Response(
        200, content=pdf_a, headers={"content-type": "application/pdf"}))
    # W2: OpenAlex points to a landing page that advertises citation_pdf_url
    respx.get("https://api.openalex.org/works/W2").mock(return_value=httpx.Response(200, json={
        "id": "https://openalex.org/W2", "open_access": {"oa_url": "https://pub.org/landing/b"}, "locations": []}))
    respx.get("https://pub.org/landing/b").mock(return_value=httpx.Response(200, headers={
        "content-type": "text/html"}, text='<meta name="citation_pdf_url" content="/pdf/b.pdf">'))
    respx.get("https://pub.org/pdf/b.pdf").mock(return_value=httpx.Response(200, content=pdf_b))
    respx.get(url__regex=r"https://api\.openalex\.org/works.*").mock(return_value=httpx.Response(
        200, json={"results": []}))
    respx.get(url__regex=r"https://api\.semanticscholar\.org/.*").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"https://export\.arxiv\.org/.*").mock(return_value=httpx.Response(
        200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>'))
    # W4 arrives via inbox, named by DOI
    (project.inbox_dir / "10.1234_d.pdf").write_bytes(make_pdf("Manually supplied paper on naps " + "x " * 80))

    res = retrieve.run(project, browser=False, log=print)
    assert res == {"inbox": 1, "open_access": 2, "browser": 0, "missing": 1}
    with project.connect() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM papers")}
        assert rows["W1"]["pdf_source"] == "unpaywall" and rows["W2"]["pdf_source"] == "openalex"
        assert rows["W3"]["pdf_status"] == "paywalled" and rows["W4"]["pdf_source"] == "manual"
        assert conn.execute("SELECT COUNT(*) FROM retrieval_attempts WHERE paper_id='W3'").fetchone()[0] >= 1
    assert (project.inbox_dir / "ingested" / "10.1234_d.pdf").exists()
    with (project.reports_dir / "missing_papers.csv").open(encoding="utf-8") as f:
        missing = list(csv.DictReader(f))
    assert [m["id"] for m in missing] == ["W3"] and missing[0]["doi_url"] == "https://doi.org/10.1234/c"

    # idempotent: second run touches nothing; the cache is shared by DOI
    res2 = retrieve.run(project, browser=False, log=lambda m: None)
    assert res2["open_access"] == 0 and res2["missing"] == 1


TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>Sleep spindles</title>
</titleStmt></fileDesc><profileDesc><abstract><div><p>We study spindles.</p></div></abstract></profileDesc>
</teiHeader><text><body>
<div><head coords="1,0,0,1,1">1. Introduction</head><p coords="1,1,1,1,1">Spindles are bursts.</p>
<p coords="2,1,1,1,1">They matter for memory.</p></div>
<div><head>2. Methods</head><p coords="3,1,1,1,1">We recorded EEG.</p>
<figure coords="3,1,1,1,1"><head>Figure 1</head><figDesc>Spindle density.</figDesc></figure></div>
</body><back><div type="acknowledgement"><div><head>Acknowledgements</head><p>Thanks.</p></div></div>
<div type="references"><listBibl><biblStruct xml:id="b0"><analytic><title>Old paper</title></analytic>
<monogr><imprint><date when="2001"/></imprint></monogr><idno type="DOI">10.5555/OLD</idno></biblStruct>
</listBibl></div></back></text></TEI>"""


def test_parse_tei():
    doc = parse_tei(TEI)
    assert doc["title"] == "Sleep spindles" and doc["abstract"] == "We study spindles."
    intro = doc["sections"][0]
    assert intro["title"] == "1. Introduction" and [p["page"] for p in intro["paragraphs"]] == [1, 2]
    assert [s["title"] for s in doc["sections"]] == ["1. Introduction", "2. Methods", "Acknowledgements"]
    assert doc["references"] == [{"id": "b0", "title": "Old paper", "doi": "10.5555/old", "year": 2001}]
    assert doc["figures"][0]["caption"] == "Figure 1 Spindle density." and doc["figures"][0]["page"] == 3


@respx.mock
def test_parse_stage(project, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_pdf("x"))
    seed_papers(project, [{"id": "W1", "title": "Sleep spindles"}, {"id": "W2", "title": "Broken"}])
    with project.connect() as conn:
        conn.execute("UPDATE papers SET pdf_status='ok', pdf_path=?", (str(pdf),))
    calls = {"n": 0}

    def grobid(request):
        calls["n"] += 1
        return httpx.Response(200, text=TEI) if calls["n"] == 1 else httpx.Response(200, text="<not-xml")
    respx.get("http://localhost:8070/api/isalive").mock(return_value=httpx.Response(200, text="true"))
    respx.post("http://localhost:8070/api/processFulltextDocument").mock(side_effect=grobid)
    project.config["parse"]["concurrency"] = 1
    res = parse.run(project, log=print)
    assert res == {"ok": 1, "failed": 1}
    ok = [f.stem.split(".")[0] for f in project.parsed_dir.glob("*.json")]
    assert len(ok) == 1
    assert json.loads((project.parsed_dir / f"{ok[0]}.json").read_text(encoding="utf-8"))["title"] == \
        "Sleep spindles"


@respx.mock
def test_parse_requires_grobid(project, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_pdf("x"))
    seed_papers(project, [{"id": "W1", "title": "T", "pdf_status": "ok"}])
    with project.connect() as conn:
        conn.execute("UPDATE papers SET pdf_status='ok', pdf_path=?", (str(pdf),))
    respx.get("http://localhost:8070/api/isalive").mock(side_effect=httpx.ConnectError("refused"))
    import pytest
    with pytest.raises(RuntimeError, match="GROBID is not reachable"):
        parse.run(project, log=lambda m: None)
