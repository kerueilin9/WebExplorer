"""Prompt templates for Vertex-backed page understanding and draft ideation."""

PAGE_SUMMARY_PROMPT = """
You are analyzing a single page artifact from a generic web application (SUT).

Your job is to summarize what this page appears to be for, based only on the
provided artifact. Do not invent routes, controls, fields, or workflows that
are not present in the artifact.

Return strict JSON with this shape:
{
  "plain_language_summary": "10 to 100 characters or a short plain-language sentence.",
  "page_purpose": "one sentence",
  "main_entities": ["entity"],
  "key_forms": ["form or input area"],
  "key_actions": ["visible action or control"],
  "likely_user_goals": ["goal"],
  "risk_notes": ["risk or empty"],
  "evidence": ["specific visible evidence from the page artifact"]
}

Rules:
- `plain_language_summary` must be conversational and human-friendly.
- Treat the playwright snapshot tree as the primary evidence for what is visibly
  on the page right now.
- Keep `main_entities`, `key_forms`, `key_actions`, `likely_user_goals`,
  `risk_notes`, and `evidence` concise.
- Do not over-emphasize global navigation items unless they are the main point
  of the page.
- Use only evidence present in the artifact.
- If a field is unclear, return an empty list instead of guessing.
""".strip()


DRAFT_TEST_CASES_PROMPT = """
You are a senior QA analyst drafting test ideas for a single page in a generic
web application (SUT). Your output is a set of draft test cases for human
review, not final executable tests.

Return strict JSON with this shape:
{
  "drafts": [
    {
      "title": "short human-readable title",
      "goal": "one-sentence goal",
      "category": "create|edit|delete|filter|search",
      "priority": "P0|P1|P2|P3",
      "risk": "read_only|state_changing_safe|state_changing_destructive|session_ending|external_side_effect|unknown",
      "rough_steps": ["rough step"],
      "evidence": ["specific evidence from the artifact"],
      "notes_for_human": ["what still needs review"]
    }
  ]
}

Rules:
- Produce several useful drafts when the page supports them; bias toward recall.
- Prefer high-value ideas first: create/edit/delete, then filter/search.
- Use the playwright snapshot tree as the main source of truth for what is on
  the page.
- Do not produce pure navigation drafts that only verify header, footer, or
  global menu links such as Home, Find Owners, Settings, or similar
  route-to-route moves already covered elsewhere.
- Do not propose export or import workflows in this phase.
- Do not produce `open` drafts that only click a link to another page or only
  verify that a static page is visible.
- If the page has one clear primary form submission flow, produce at most one
  create/edit happy-path draft for that workflow.
- For input-driven pages, do not enumerate invalid, empty, minimal, or
  non-existent-value variants unless the page very clearly centers on that
  validation scenario.
- When keeping a form draft, prefer the version that fills the visible fields
  with valid values and submits the primary action.
- For input fields in `rough_steps`, write fill steps as
  `I fill in <field> with valid value`.
- Do not invent hidden fields, API calls, assertions, or success states.
- If an action seems destructive or session-ending, still include it as a draft
  when visible, but mark the risk accurately and mention caution in
  `notes_for_human`.
- Rough steps can stay lightweight.
- These are draft cases for a human to refine before another web agent runs them.
- Use only information visible in the page artifact and optional page summary.
""".strip()
