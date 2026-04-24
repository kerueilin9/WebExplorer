---
name: action-review-task-workflow
description: Run a generic SUT Vertex-backed draft-case workflow from browser observations to a prioritized draft backlog.
compatibility: google-adk>=1.31.0,<2.0
---

# Action Review Task Workflow

Use this skill when the user asks to generate page-level draft cases from a
generic SUT. Keep the workflow generic across SUTs. The preferred path is
Vertex-backed and evidence-first: tools collect page observations, summarize
each observed page, propose draft cases from the full page artifact, and merge
those drafts into a prioritized backlog for human refinement.

## Required Inputs

- `worklist_path`: a canonical action worklist from
  `build_action_discovery_worklist`.
- If `worklist_path` is unavailable, first create it from a stable route
  manifest.

## Optional Inputs

- `site_name`: short SUT name used for task ids.
- `output_root`: where observations, summaries, drafts, and backlog files are written.
- `storage_state_path`: authenticated browser state used while observing authenticated routes.
- `start_url`: expected task start URL. Omit it when the worklist already includes a start URL.
- `manifest_path`: route manifest used to bootstrap browser-backed action discovery.
- `observations_path`: optional output path for the observation index.
- `summaries_path`: optional output path for the per-page summary index.
- `drafts_path`: optional output path for the per-page draft index.
- `backlog_path`: optional output path for the consolidated prioritized backlog.
- route/page budget limits and optional category filters for backlog merge.

## Execution Rules

1. Build or reuse a canonical worklist with `build_action_discovery_worklist`.
   This step only dedupes routes and folds query-string variants. It must not
   decide what page actions exist.
2. Call `observe_task_pages_from_worklist` to open each canonical route and
   write `page-*.yml` observations. Observations should include visible
   headings, text, forms, tables, route provenance, and the raw
   `playwright-cli snapshot` tree, but no heuristic action classification.
3. Call `summarize_pages_with_vertex` to generate one `page-*.summary.json`
   file per observed page. Each summary should include a short
   `plain_language_summary` plus structured fields such as page purpose,
   entities, key forms/actions, likely user goals, and risk notes.
4. Call `draft_test_ideas_with_vertex` to generate one `page-*.drafts.json`
   file per observed page. Drafts may be short and incomplete, but each draft
   should include category, risk, priority, rough goal, route, evidence, and
   notes for human refinement. Do not keep pure `navigate` drafts or simple
   `open` drafts that only exercise site-wide navigation or page-entry links
   already covered elsewhere. For pages with one clear primary form workflow,
   keep one happy-path draft instead of many invalid/empty/minimal variants.
5. Call `merge_page_drafts` to dedupe page-level drafts, normalize
   category/risk/priority, and produce `draft_backlog.json`.
6. Stop at the draft backlog. This system does not generate final executable
   action tasks. The backlog is intended for human review and downstream
   execution in another web agent.

## LLM-First Discovery Sequence

Use this sequence when action tasks are not ready yet:

1. Build canonical action discovery worklist from route manifest:
  - `build_action_discovery_worklist(manifest_path, output_path, site_name, ...)`
2. Observe each canonical route without pre-classifying actions:
  - `observe_task_pages_from_worklist(worklist_path, output_path, observation_dir, site_name, storage_state_path, ...)`
3. Summarize each page artifact with Vertex:
  - `summarize_pages_with_vertex(observation_index_path, summary_index_path, summary_output_dir, site_name, ...)`
4. Draft page-level test ideas with Vertex:
  - `draft_test_ideas_with_vertex(observation_index_path, draft_index_path, draft_output_dir, summary_index_path, site_name, ...)`
5. Merge and dedupe drafts into one backlog:
  - `merge_page_drafts(draft_index_path, output_path, site_name, ...)`

## Draft Task Shape

When producing page-level draft cases, use this shape. Drafts do not need final
assertions or executable task JSON:

```json
{
  "page_id": "page-001",
  "route": "/users/add",
  "plain_language_summary": "這頁主要是新增員工資料的表單頁面。",
  "drafts": [
    {
      "draft_id": "timeoff_users_add_create_employee",
      "title": "Create employee",
      "goal": "建立新員工資料",
      "category": "create",
      "priority": "P0",
      "risk": "state_changing_safe",
      "rough_steps": [
        "Open Add employee page.",
        "Fill required employee fields.",
        "Submit the form."
      ],
      "evidence": [
        "The page shows an Add employee heading.",
        "A create button is visible."
      ],
      "notes_for_human": [
        "Need disposable employee test data.",
        "Success assertion is not finalized yet."
      ]
    }
  ]
}
```

Prefer category values: `create`, `edit`, `delete`, `filter`, `search`,
`export`, `import`, `auth_session`, or `unknown`.

Keep `priority` separate from downstream execution order. `delete` can be high
priority because it matters, but it should remain a draft until a later system
decides how to execute it safely.

## Safe Defaults

- action worklist file: `action_worklist.generic.json`
- observation index file: `page_observations.index.json`
- observation dir: `page_observations`
- page summary index file: `page_summaries.index.json`
- page summary dir: `page_summaries`
- page draft index file: `page_drafts.index.json`
- page draft dir: `page_drafts`
- backlog file: `draft_backlog.json`

## Example

```text
Build action discovery worklist from timeoff/route_manifest.auth.generic.json into timeoff/action_worklist.auth.generic.json.

Observe task pages from timeoff/action_worklist.auth.generic.json into timeoff/page_observations.index.json. Write route observations under timeoff/page_observations.

Summarize the page observations with Vertex into timeoff/page_summaries.index.json and timeoff/page_summaries.

Draft page-level test ideas with Vertex into timeoff/page_drafts.index.json and timeoff/page_drafts.

Merge timeoff/page_drafts.index.json into timeoff/draft_backlog.json for human review. Do not generate final executable action tasks in this workflow.
```
