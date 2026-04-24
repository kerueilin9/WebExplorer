# Browser-Backed Action Discovery Design

## Decision

The action discovery phase should not be a static manifest scanner.

Static extraction is useful as a cheap hinting layer, but it cannot reliably
author workflow tasks because it does not see the live UI state after navigation,
menus, overlays, disabled controls, validation messages, or role-dependent
content.

The target design is a browser-backed discovery pass:

1. Read the stable guest/auth route manifests.
2. Normalize routes into a canonical route worklist.
3. Open each canonical route with `playwright-cli`.
4. Capture snapshot, visible DOM summary, headings, forms, and safe controls.
5. Write full-page observation artifacts such as `page-*.yml`.
6. Send each observed page artifact to Vertex for page summarization.
7. Send each observed page artifact plus summary to Vertex for draft case ideation.
8. Merge and dedupe page drafts into a prioritized draft backlog for human review.

This keeps route crawling deterministic while allowing workflow task authoring to
use the same visual/browser reasoning expected from a human test author.

## Why Not Static Intent Extraction

The current static approach inspects only route metadata already present in the
manifest. It misses or misclassifies cases such as:

- a `Create` button that only appears after opening a dropdown
- a modal form that does not have its own URL
- disabled actions that should not become tasks
- pages where the link label differs from the page heading
- role-specific controls visible only after authenticated navigation
- forms whose real required fields are visible only after the page loads

Static metadata may remain as a prefilter, but it should not be the source of
truth for action task generation.

## Agent Architecture

Use two cooperating roles.

### Root Workflow Agent

Responsibilities:

- run guest/auth route crawls
- generate route-level navigation tasks
- build the canonical action-discovery route worklist
- assign one route at a time to the action-discovery phase
- merge outputs and validation summaries
- enforce global policies such as max routes, max actions per route, and unsafe action blocking

### Browser Action Discovery Agent

Responsibilities:

- open a canonical route in a headed persistent `playwright-cli` session
- inspect the live page using snapshots and structured DOM summaries
- capture evidence-rich page artifacts rather than pre-deciding actions too early
- click only safe controls needed to reveal workflow UI
- avoid submit, save, delete, approve, reject, import, upload, or signout actions unless an explicit policy allows them
- produce an evidence record that a separate summarization / draft ideation phase can consume

This can be implemented as a second ADK agent/sub-agent when the workflow becomes
tool-rich. For the first implementation, it can also be a dedicated subflow in
the root agent with a stricter prompt and narrower tool access. The important
boundary is behavioral: route crawling is deterministic; action discovery is
browser-observational and evidence-backed.

Recommended progression:

1. Implement the browser-backed discovery as a separate tool/subflow callable by the root workflow.
2. Keep one browser session and one route at a time until behavior is stable.
3. Promote it to a dedicated ADK sub-agent if prompt/tool separation is needed.
4. Add parallel route workers only after session isolation and output merging are reliable.

## Canonical Route Worklist

Action discovery should not visit every URL variant. It should explore one
canonical route per path.

Canonicalization rules:

- strip query strings for action discovery worklist keys
- strip fragments
- normalize trailing slashes
- keep only same-origin routes
- exclude logout/signout/session-ending routes
- exclude unsafe or destructive routes
- prefer the queryless route when both query and queryless variants exist
- if only query variants exist, use the queryless path as the candidate only when it is reachable or safe to `goto`

Example:

```text
http://localhost:3102/calendar/teamview?department=1&date=2026-03
http://localhost:3102/calendar/teamview
```

Only this canonical route should enter action discovery:

```text
http://localhost:3102/calendar/teamview
```

Query-string variants should be recorded as skipped or folded variants, not
opened by the action-discovery agent and not used to generate separate action
tasks.

Suggested worklist record:

```json
{
  "canonical_path": "/calendar/teamview",
  "selected_route_id": "timeoff_authenticated_page_calendar_teamview",
  "selected_url": "http://localhost:3102/calendar/teamview",
  "folded_variants": [
    "/calendar/teamview?department=1&date=2026-03"
  ],
  "phase": "authenticated",
  "require_login": true,
  "navigation_steps": [
    "I open the configured home page",
    "I click the \"Team View\" link to reach \"/calendar/teamview\""
  ]
}
```

## Per-Route Browser Exploration Loop

For each canonical route:

1. Start from the configured home page or authenticated storage state.
2. Navigate using the recorded route navigation steps when reliable; otherwise use `goto` as a fallback and mark the route as direct navigation.
3. Capture snapshot and DOM summary.
4. Record visible headings, forms, tables/lists, filters, and primary controls.
5. Classify safe controls.
6. Optionally click safe reveal controls:
   - menu
   - dropdown
   - tab
   - drawer opener
   - modal opener such as `New`, `Create`, or `Edit`
7. After each safe click, capture another snapshot and DOM summary.
8. Generate task drafts only from observed evidence.
9. Return to the route baseline before exploring the next control.

Implemented behavior:

- `build_action_discovery_worklist` folds query-string variants into one canonical route per path.
- `observe_task_pages_from_worklist` opens each canonical route and writes `page-*.yml` observations with visible text, headings, controls, forms, tables, snapshot paths, and route provenance.
- Authenticated routes that land on a login page are marked as `auth_redirect_detected` instead of producing false guest-page evidence.
- `summarize_pages_with_vertex` generates one `page-*.summary.json` file per observed page, including a short `plain_language_summary`.
- `draft_test_ideas_with_vertex` generates one `page-*.drafts.json` file per observed page.
- `merge_page_drafts` dedupes page drafts, classifies category/risk/priority, and writes `draft_backlog.json`.
- The workflow stops at the draft backlog. Final executable action tasks belong to a downstream system.

Hard stop conditions:

- URL leaves the selected origin
- action is destructive or session-ending
- route becomes logged out unexpectedly
- same control loops without UI change
- max safe clicks per route is reached
- max task drafts per route is reached

## Safe vs Unsafe Actions

Allowed by default:

- open menus and dropdowns
- switch tabs
- open create/edit/details modal without submitting
- type into search boxes only when no submit/write action is required
- use read-only filters when the result stays on the same canonical path
- open details links that stay within the canonical route family

Blocked by default:

- submit/save/create/update outside explicit action-task confirmation mode
- delete/remove/archive/disable/purge/reset/restart
- approve/reject workflows
- import/export/upload/download
- logout/signout
- admin settings save
- payment or checkout flows

## Outputs

The Vertex-backed phase should produce observations, page summaries, page
drafts, and a prioritized backlog before any downstream execution.

Recommended artifacts:

```text
<output_root>/
  action_worklist.auth.generic.json
  page_observations.index.json
  page_observations/
    page-001-<route>.yml
  page_summaries.index.json
  page_summaries/
    page-001.summary.json
  page_drafts.index.json
  page_drafts/
    page-001.drafts.json
  draft_backlog.json
```

`page-*.yml` should include:

- route provenance
- canonical path
- folded query variants
- baseline heading/title
- observed controls
- visible page text
- visible forms and field labels
- visible tables/list summaries
- evidence snapshot paths
- `page_id`

`page-*.summary.json` should include:

- `plain_language_summary` in natural language
- page purpose
- main entities
- key forms
- key actions
- likely user goals
- risk notes
- evidence highlights

`page-*.drafts.json` should include:

- page-level candidate test cases
- category, risk, and priority
- rough steps
- evidence
- notes for human refinement

Draft backlog items are not final task files. They are an operator-facing,
human-reviewable inventory for downstream execution systems such as AgentOccam.

The backlog should keep priority separate from execution order. Create/edit and
delete can be high-priority coverage targets, but destructive delete should run
last and only as a dry-run confirmation or against disposable test data.

## Task Authoring Rules

Draft cases must be grounded in page evidence:

- scenario name comes from visible UI intent, not just URL tokens
- steps must replay from home page or stored auth state to the route
- form field names should come from visible labels, placeholders, names, or aria labels
- assertions should prefer visible headings, modal titles, field labels, table/list text, or success UI evidence
- query-string variants must not produce separate action tasks
- unsafe or ambiguous actions should be recorded as skipped or low-confidence drafts, not promoted into executable tasks here

For non-submitting modal entrypoints, generate a task such as:

```text
Open Create Employee dialog
```

Do not claim the workflow is fully executable unless a later downstream system
proves it. This stage only drafts the case.

## Interaction With Navigation Tasks

Route-level navigation task generation remains manifest-driven and deterministic.

Action draft generation becomes Vertex-backed and tool-governed:

```text
manifest -> canonical route worklist -> page observations -> page summaries -> page drafts -> draft backlog
```

This prevents the static route manifest from pretending to know page workflows it
has not actually observed, while also preventing heuristic tools from deciding
too early what the page actions mean.

## Recommended Skill Split

Keep the existing `manifest-first-route-workflow` skill focused on route crawl
and navigation task generation.

Use a second skill for browser-backed action review and draft-case generation:

```text
action-review-task-workflow
```

Suggested prompt:

```text
Run the action-review-task-workflow skill for <sut_name>.
Use worklist_path <output_root>/action_worklist.auth.generic.json, output_root <output_root>, storage_state_path <state_path>,
and start_url <start_url>. First observe pages, summarize each page with Vertex, write page-level draft cases, and merge a prioritized draft backlog. Do not generate final executable action tasks in this workflow.
```

This split keeps route coverage and action workflow authoring independently
debuggable. It also makes it easier to re-run action discovery after changing
task-authoring policies without re-crawling the whole SUT.
