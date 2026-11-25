"""Stage 1: research questions. `draft` (LLM) -> rqs.draft.yaml -> human edit -> `approve` -> rqs.yaml."""
from __future__ import annotations

import json

import yaml
from pydantic import BaseModel, Field, field_validator

from ..llm import get_provider
from ..project import Project
from ..util import now_iso, parse_json_block


class ResearchQuestion(BaseModel):
    id: str = ""
    question: str
    rationale: str = ""
    sub_questions: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    inclusion: list[str] = Field(default_factory=list)
    exclusion: list[str] = Field(default_factory=list)
    undermind_prompt: str = ""

    @field_validator("sub_questions", "keywords", "inclusion", "exclusion", mode="before")
    @classmethod
    def _listify(cls, v):
        if v is None:
            return []
        return [v] if isinstance(v, str) else v


SYSTEM = """You are an expert research librarian helping a researcher scope a systematic literature review.
Return ONLY JSON, no prose."""

PROMPT = """Below is a researcher's concept note. Propose between {lo} and {hi} central research questions (RQs)
for a literature review that together cover the note's scope with minimal overlap.

For each RQ return an object with:
- "question": one precise sentence
- "rationale": why it matters for the concept note (1-2 sentences)
- "sub_questions": 2-5 narrower questions
- "keywords": key terms and synonyms (5-15)
- "inclusion": criteria for papers to include
- "exclusion": criteria for papers to exclude
- "undermind_prompt": a detailed, self-contained natural-language paragraph (120-250 words) describing exactly
  which papers are wanted for this RQ and why, written for an autonomous literature-search agent that sees ONLY
  this text. Include the relevant context from the concept note. Do not use keyword lists or boolean queries.

Return {{"rqs": [ ... ]}}.

<concept_note>
{concept}
</concept_note>"""

HEADER = """# Draft research questions. Edit freely (add, remove, reword), then run:
#   uv run lit rq approve -p {slug}
# IDs are renumbered RQ1..RQn on approval, in the order below.
"""


def load_rqs(path) -> list[ResearchQuestion]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = data.get("rqs", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a list under 'rqs'")
    return [ResearchQuestion.model_validate(i) for i in items]


def dump_rqs(rqs: list[ResearchQuestion], extra: dict | None = None) -> str:
    body = {**(extra or {}), "rqs": [r.model_dump() for r in rqs]}
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=110)


def draft(project: Project, *, overwrite: bool = False) -> list[ResearchQuestion]:
    if project.rqs_draft.exists() and not overwrite:
        raise FileExistsError(f"{project.rqs_draft} exists; pass --overwrite to regenerate it")
    concept = project.concept.read_text(encoding="utf-8")
    cfg = project.config
    llm = get_provider("fast", cfg)
    reply = llm.complete([{"role": "user", "content": PROMPT.format(
        lo=cfg["rq"]["min_questions"], hi=cfg["rq"]["max_questions"], concept=concept)}],
        system=SYSTEM, max_tokens=8000)
    data = parse_json_block(reply)
    rqs = [ResearchQuestion.model_validate(i) for i in (data["rqs"] if isinstance(data, dict) else data)]
    for i, r in enumerate(rqs, 1):
        r.id = f"RQ{i}"
    project.rqs_draft.write_text(HEADER.format(slug=project.slug) + dump_rqs(rqs), encoding="utf-8")
    return rqs


def approve(project: Project) -> list[ResearchQuestion]:
    if not project.rqs_draft.exists():
        raise FileNotFoundError(f"{project.rqs_draft} not found; run `lit rq draft` or the generate-rqs skill")
    rqs = load_rqs(project.rqs_draft)
    if not rqs:
        raise ValueError("No research questions in the draft")
    for i, r in enumerate(rqs, 1):
        r.id = f"RQ{i}"
        if not r.undermind_prompt.strip():
            r.undermind_prompt = r.question + (" " + r.rationale if r.rationale else "")
    project.rqs.write_text(dump_rqs(rqs, {"approved_at": now_iso()}), encoding="utf-8")
    with project.connect() as conn:
        keep = ",".join("?" * len(rqs))
        conn.execute(f"DELETE FROM paper_rq WHERE rq_id NOT IN ({keep})", [r.id for r in rqs])
        conn.execute(f"DELETE FROM rqs WHERE id NOT IN ({keep})", [r.id for r in rqs])
        for r in rqs:
            conn.execute("INSERT INTO rqs (id, text, sub_questions_json, keywords_json) VALUES (?,?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET text=excluded.text, "
                         "sub_questions_json=excluded.sub_questions_json, keywords_json=excluded.keywords_json",
                         (r.id, r.question, json.dumps(r.sub_questions), json.dumps(r.keywords)))
    return rqs


def run(project: Project, action: str, **kw) -> list[ResearchQuestion]:
    return {"draft": draft, "approve": approve}[action](project, **kw)
