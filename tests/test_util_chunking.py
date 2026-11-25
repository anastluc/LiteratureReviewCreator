from litkb.chunking import chunk_document, chunk_markdown, chunk_paragraphs
from litkb.query import extract_citations, validate_citations
from litkb.stages.report import check_citations
from litkb.util import (
    approx_tokens,
    fallback_paper_id,
    normalize_doi,
    normalize_title,
    parse_json_block,
    reconstruct_abstract,
)

CCFG = {"target_tokens": 100, "overlap_tokens": 20, "min_tokens": 15,
        "drop_sections": ["references", "acknowledgements"]}


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1038/Nature14539") == "10.1038/nature14539"
    assert normalize_doi("doi:10.1000/xyz123.") == "10.1000/xyz123"
    assert normalize_doi("no doi here") is None
    assert normalize_doi(None) is None


def test_normalize_title_and_fallback_id():
    assert normalize_title("Deep  Learning: <i>A</i> Review — Café!") == "deep learning a review cafe"
    a = fallback_paper_id(None, "Deep learning: a review")
    assert a == fallback_paper_id(None, "deep LEARNING a review") and a.startswith("h") and len(a) == 11


def test_reconstruct_abstract_and_json():
    assert reconstruct_abstract({"Hello": [0], "world": [1], "again": [3], "big": [2]}) == "Hello world big again"
    assert parse_json_block('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_block('prefix {"rqs": [1, 2]} suffix') == {"rqs": [1, 2]}


def test_chunk_paragraphs_merges_and_splits():
    short = [{"text": f"Short paragraph number {i} about sleep.", "page": i} for i in range(1, 6)]
    chunks = chunk_paragraphs(short, **{"target": 100, "overlap": 20, "min_tokens": 15})
    assert len(chunks) == 1 and chunks[0]["page"] == 1
    long_text = " ".join(f"Sentence {i} discusses memory consolidation during slow wave sleep." for i in range(60))
    chunks = chunk_paragraphs([{"text": long_text, "page": 3}], 100, 20, 15)
    assert len(chunks) > 3
    assert all(approx_tokens(c["text"]) <= 130 for c in chunks)
    # overlap: consecutive chunks share a sentence
    assert chunks[0]["text"].split(". ")[-1][:20] in chunks[1]["text"]


def test_chunk_document_drops_sections():
    doc = {"sections": [
        {"title": "1. Introduction", "paragraphs": [{"text": "Intro text " * 30, "page": 1}]},
        {"title": "Acknowledgements", "paragraphs": [{"text": "We thank our funders " * 10, "page": 9}]},
        {"title": "References", "paragraphs": [{"text": "[1] Someone 2020", "page": 10}]},
    ]}
    chunks = chunk_document(doc, CCFG)
    assert {c["section"] for c in chunks} == {"1. Introduction"}


def test_chunk_markdown_by_heading():
    md = "# Report\nOverview text that is long enough to stand alone as a chunk for sure, really.\n" \
         "## Findings\nFinding one is important and documented in several cohort studies of adults.\n"
    parts = chunk_markdown(md, CCFG)
    assert [p["section"] for p in parts] == ["Report", "Findings"]


def test_citations():
    text = "Sleep helps [W123]. Also [W123, h0123456789]; but not [Smith 2020] or [W9; W10]."
    assert extract_citations(text) == ["W123", "h0123456789", "W9", "W10"]
    ok, bad = validate_citations(text, {"W123", "W9"})
    assert ok == ["W123", "W9"] and bad == ["h0123456789", "W10"]
    fixed, bad = check_citations(text, {"W123", "W9", "W10"})
    assert "[W123, ?h0123456789]" in fixed and bad == ["h0123456789"]
