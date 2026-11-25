---
name: generate-rqs
description: Generate central research questions (RQs) from a project's concept.md and write rqs.draft.yaml for human review. Use when the user asks to create or refine research questions for a litkb project.
---

# Generate research questions

1. Ask for the project slug if not given; read `projects/<slug>/concept.md`.
2. Draft 3–6 central RQs (config `[rq]`). For each RQ write:
   - `id` (RQ1, RQ2, …), `question` (one sentence), `rationale` (why it matters for the concept note),
   - `sub_questions` (2–5), `keywords` and synonyms, `inclusion` / `exclusion` hints,
   - `undermind_prompt`: a self-contained paragraph (120–250 words) stating what the researcher is trying to
     understand for this RQ and which papers would be central. Undermind's deep-search agents see ONLY this
     text, so include the needed concept-note context; no keyword lists, journal names or boolean queries.
3. Ensure RQs are complementary (minimal overlap) and together cover the concept note's scope.
4. Write `projects/<slug>/rqs.draft.yaml`, then STOP: show the RQs to the user and ask for edits.
5. Only when the user approves, run `uv run lit rq approve -p <slug>`.

(Non-interactive alternative: `uv run lit rq draft -p <slug>` uses the configured LLM provider.)
