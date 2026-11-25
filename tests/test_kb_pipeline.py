import json

import yaml
from conftest import seed_papers
from fastapi.testclient import TestClient

from litkb import kb
from litkb.query import answer
from litkb.stages import index, mapping, report

TOPICS = {
    "spindle": "sleep spindles thalamocortical oscillation memory consolidation eeg",
    "replay": "hippocampal replay rodents place cells sharp wave ripple",
    "insomnia": "insomnia cognitive behavioural therapy clinical trial adults",
}


def build_corpus(project):
    papers, links = [], {}
    n = 0
    for t, words in TOPICS.items():
        for i in range(5):
            n += 1
            pid = f"W{n}"
            papers.append({"id": pid, "title": f"{t.title()} study {i}: {words.split()[i]}",
                           "abstract": f"{words} {words} variant {i}.", "year": 2010 + n, "cited_by_count": n,
                           "doi": f"10.5555/{pid.lower()}"})
            links[pid] = ["RQ1"] if t != "insomnia" else ["RQ2"]
    seed_papers(project, papers, links)
    with project.connect() as conn:
        conn.execute("UPDATE papers SET pdf_status='ok', parse_status='ok' WHERE id='W1'")
        conn.execute("INSERT INTO citations VALUES ('W2', 'W1')")
    (project.parsed_dir / "W1.json").write_text(json.dumps({"sections": [
        {"title": "Results", "paragraphs": [{"text": "Spindle density predicted overnight memory retention "
                                                     "in all participants. " * 6, "page": 4}]},
        {"title": "References", "paragraphs": [{"text": "[1] x", "page": 9}]}]}), encoding="utf-8")
    (project.undermind_dir / "RQ1").mkdir(parents=True, exist_ok=True)
    (project.undermind_dir / "RQ1" / "report.md").write_text(
        "# Summary\nSpindles and replay both support consolidation according to many studies here.\n",
        encoding="utf-8")
    project.rqs.write_text(yaml.safe_dump({"rqs": [
        {"id": "RQ1", "question": "How do spindles and replay support memory?", "sub_questions": ["EEG?"]},
        {"id": "RQ2", "question": "Does treating insomnia improve cognition?"}]}), encoding="utf-8")


def test_index_query_map_report_api(project, fake_llm):
    build_corpus(project)
    res = index.run(project, log=lambda m: None)
    assert res["papers"] == 15 and res["report_chunks"] == 1
    chunks = kb.open_table(kb.connect(project), kb.CHUNKS).to_arrow().to_pylist()
    kinds = {c["kind"] for c in chunks}
    assert kinds == {"abstract", "fulltext", "undermind_report"}
    assert not any(c["section"] == "References" for c in chunks)
    full = [c for c in chunks if c["kind"] == "fulltext"][0]
    assert full["page"] == 4 and full["rq_ids"] == ["RQ1"]

    # idempotent re-run: nothing re-chunked
    assert index.run(project, log=lambda m: None)["new_chunks"] == 0

    # hybrid search honours filters
    hits = kb.search_chunks(project, "insomnia therapy", k=5)
    assert hits[0]["paper_id"] in {"W11", "W12", "W13", "W14", "W15"}
    only = kb.search_chunks(project, "insomnia therapy", k=5, rq_ids=["RQ1"])
    assert all("RQ1" in h["rq_ids"] for h in only)
    sel = kb.search_chunks(project, "memory", k=5, paper_ids=["W3"])
    assert {h["paper_id"] for h in sel} == {"W3"}

    out = answer(project, "What predicts memory retention?")
    assert out["citations"] and not out["invalid_citations"]

    m = mapping.run(project, log=lambda m: None)
    assert m["papers"] == 15 and m["clusters"] >= 2
    clusters = json.loads((project.map_dir / "clusters.json").read_text(encoding="utf-8"))
    assert all(c["label"] for c in clusters)
    assert all(p["cluster_id"] is not None for p in kb.all_papers(project))

    fake_llm.replies = [("Write the report section", "### Answer\nYes [W1, W999]."),
                        ("Executive summary", "## Executive summary\nOverall [W2].")]
    r = report.run(project, log=lambda m: None)
    by_rq = (project.reports_dir / "report_by_rq.md").read_text(encoding="utf-8")
    assert "## RQ1." in by_rq and "[W1, ?W999]" in by_rq and "## References" in by_rq
    assert "**[W1]** Lovelace, A., Turing, A. (2011)" in by_rq
    assert r["report_by_rq_invalid"] == ["W999"]
    assert (project.reports_dir / "report_thematic.md").read_text(encoding="utf-8").count("## Theme") >= 2

    # API
    from litkb.api.app import app
    c = TestClient(app)
    assert c.get("/api/projects").json()[0]["slug"] == "demo"
    papers = c.get("/api/demo/papers", params={"rq": "RQ2"}).json()
    assert len(papers) == 5 and papers[0]["cluster_id"] is not None
    detail = c.get("/api/demo/papers/W1").json()
    assert detail["cited_by"] == ["W2"] and detail["abstract"]
    mp = c.get("/api/demo/map", params={"method": "tsne"}).json()
    assert len(mp["points"]) == 15 and mp["clusters"][0]["centroid"]
    assert c.get("/api/demo/citations").json() == [{"source": "W2", "target": "W1"}]
    assert set(c.get("/api/demo/reports").json()) >= {"report_by_rq", "report_thematic"}
    assert c.get("/api/demo/reports/report_by_rq").json()["markdown"].startswith("# Literature review")
    assert c.get("/api/demo/reports/..%2Fconcept").status_code == 404
    assert c.get("/api/demo/pdf/W1").status_code == 404   # pdf_path missing on disk
    q = c.post("/api/demo/query", json={"question": "memory retention", "stream": False}).json()
    assert q["sources"] and q["citations"]
    with c.stream("POST", "/api/demo/query", json={"question": "memory retention"}) as s:
        body = "".join(s.iter_text())
    assert "event: sources" in body and "event: token" in body and "event: done" in body
