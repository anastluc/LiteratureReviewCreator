import json

import httpx
import pandas as pd
import respx
import yaml

from litkb.stages import merge, rq

RQ_JSON = json.dumps({"rqs": [
    {"question": "Does sleep improve declarative memory?", "sub_questions": ["Which stage?"],
     "keywords": "sleep", "undermind_prompt": "Find papers on sleep and declarative memory."},
    {"question": "What mechanisms link sleep spindles and memory?", "rationale": "Mechanism."},
]})


def test_rq_draft_and_approve(project, fake_llm):
    fake_llm.replies = [("concept note", "```json\n" + RQ_JSON + "\n```")]
    rqs = rq.run(project, action="draft")
    assert [r.id for r in rqs] == ["RQ1", "RQ2"]
    assert rqs[0].keywords == ["sleep"]  # str coerced to list
    text = project.rqs_draft.read_text(encoding="utf-8")
    assert text.startswith("# Draft research questions")
    # human edit: drop RQ1, add one
    data = yaml.safe_load(text)
    data["rqs"] = data["rqs"][1:] + [{"question": "Is the effect age-dependent?"}]
    project.rqs_draft.write_text(yaml.safe_dump(data), encoding="utf-8")
    approved = rq.run(project, action="approve")
    assert [(r.id, r.question[:5]) for r in approved] == [("RQ1", "What "), ("RQ2", "Is th")]
    assert approved[1].undermind_prompt == "Is the effect age-dependent?"
    frozen = yaml.safe_load(project.rqs.read_text(encoding="utf-8"))
    assert "approved_at" in frozen and len(frozen["rqs"]) == 2
    with project.connect() as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM rqs ORDER BY id")] == ["RQ1", "RQ2"]


def _write_csv(project, rq_id, rows):
    d = project.undermind_dir / rq_id
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "papers.csv", index=False)


def _work(wid, doi, title, refs=(), year=2020):
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}" if doi else None,
            "display_name": title, "publication_year": year, "cited_by_count": 10,
            "primary_location": {"source": {"display_name": "J. Sleep"}, "landing_page_url": "https://x.org/a"},
            "authorships": [{"author": {"display_name": "Jane Doe"}}],
            "open_access": {"is_oa": True, "oa_url": "https://x.org/a.pdf"},
            "abstract_inverted_index": {"Sleep": [0], "matters": [1]},
            "referenced_works": [f"https://openalex.org/{r}" for r in refs], "ids": {}, "locations": []}


@respx.mock
def test_merge_dedupe_enrich_and_approve(project):
    _write_csv(project, "RQ1", [
        {"Title": "Sleep and memory", "DOI": "10.1234/a", "Year": "2020", "Authors": "Jane Doe; Bob Roe",
         "Relevance": "95%"},
        {"Title": "Spindles in Sleep", "DOI": "", "Year": "2019", "Authors": "A B"},
        {"Title": "Unmatched obscure paper", "DOI": "", "Year": "2001", "Authors": ""},
    ])
    _write_csv(project, "RQ2", [
        {"Title": "Spindles in sleep.", "DOI": "", "Year": "2019"},          # title duplicate
        {"Title": "SLEEP AND MEMORY", "DOI": "https://doi.org/10.1234/A", "Year": "2020"},  # doi duplicate
    ])

    def search(request):
        q = request.url.params.get("search", "")
        if "spindles" in q:
            return httpx.Response(200, json={"results": [_work("W2", "10.1234/b", "Spindles in sleep", ["W1"], 2019)]})
        return httpx.Response(200, json={"results": []})

    respx.get("https://api.openalex.org/works", params__contains={"filter": "doi:10.1234/a"}).mock(
        return_value=httpx.Response(200, json={"results": [_work("W1", "10.1234/a", "Sleep and Memory", ["W2"])]}))
    respx.get("https://api.openalex.org/works").mock(side_effect=search)

    df = merge.run(project, action="merge")
    assert len(df) == 3
    by_id = df.set_index("id")
    assert by_id.loc["W1", "rq_ids"] == "RQ1;RQ2" and by_id.loc["W1", "abstract"] == "Sleep matters"
    assert by_id.loc["W1", "undermind_scores"].startswith("RQ1:0.950")
    assert by_id.loc["W2", "rq_ids"] == "RQ1;RQ2" and by_id.loc["W2", "doi"] == "10.1234/b"
    fallback = [i for i in df["id"] if i.startswith("h")]
    assert len(fallback) == 1

    # human prunes the fallback paper; re-running merge keeps the decision
    df.loc[df["id"] == fallback[0], "include"] = 0
    df.to_csv(project.papers_review, index=False)
    df2 = merge.run(project, action="merge")
    assert str(df2.set_index("id").loc[fallback[0], "include"]) == "0"

    res = merge.run(project, action="approve")
    assert res == {"papers": 3, "included": 2, "citations": 2}
    with project.connect() as conn:
        rqs = {(r[0], r[1]) for r in conn.execute("SELECT paper_id, rq_id FROM paper_rq")}
        assert ("W1", "RQ2") in rqs and ("W2", "RQ1") in rqs
        assert json.loads(conn.execute("SELECT authors_json FROM papers WHERE id='W1'").fetchone()[0]) == \
            ["Jane Doe"]
