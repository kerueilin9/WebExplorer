# ADK Playwright Agent Scaffold

This directory contains a starter scaffold for an Agent Development Kit (ADK) project that can:

- drive `playwright-cli` through structured tools
- explore a target web application
- read and write project files
- generate route manifests and task JSON files
- validate generated task outputs

This is a project skeleton. It includes real module boundaries and usable tool wrappers, but the orchestration prompt and output generation logic are still intentionally conservative.

## Layout

```text
adk_playwright_agent/
  agent.py
  app/
  tools/
  adapters/
  docs/
  templates/
  eval/
  pyproject.toml
```

## Install

```powershell
cd D:\Ker\Desktop\Document\other\GUI_test\adk_playwright_agent
uv sync
```

This project tracks the stable ADK 1.x line with `google-adk>=1.31.0,<2.0`.
ADK 2.0 is currently documented upstream as Alpha / pre-GA, so it is not used
by default unless the project explicitly opts into a migration branch.

## Configuration

Copy `.env.example` to `.env` if needed, or edit the existing `.env`.

Important settings:

- `ADK_MODEL`
- `AGENT_WORKSPACE_ROOT`
- `PLAYWRIGHT_CLI_BIN`
- `DEFAULT_CREDENTIALS_FILE`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_GENAI_USE_VERTEXAI`
- `VERTEX_MODEL`

Credentials lookup is recoverable and tries configured paths first, then falls
back to `AGENT_WORKSPACE_ROOT/passwords.txt`, this project directory's
`passwords.txt`, and the current working directory's `passwords.txt`. Missing
files or missing system names are returned as tool errors instead of crashing the
ADK process.

Profile registry lookup is also recoverable. For relative profile paths it first
checks under `AGENT_WORKSPACE_ROOT` (for example `profiles/sut_profiles.json`),
then falls back to `<workspace>/adk_playwright_agent/profiles/sut_profiles.json`.

## Run With uv

Interactive CLI:

```powershell
uv run adk run .
```

Web UI:

```powershell
uv run adk web ..
```

Python compile check:

```powershell
uv run python -m compileall agent.py app tools adapters scripts
```

Manifest smoke test:

```powershell
uv run python scripts/manifest_smoke.py
```

Context memory smoke test:

```powershell
uv run python scripts/context_memory_smoke.py
```

Crawler manifest helper smoke test:

```powershell
uv run python scripts/crawler_manifest_smoke.py
```

Manifest-first workflow smoke test:

```powershell
uv run python scripts/workflow_smoke.py
```

Profile JSON parameter smoke test:

```powershell
uv run python -c "from adk_playwright_agent.tools.workspace_tools import read_json_file; import json; data=None; source=None; candidates=('profiles/sut_profiles.json','adk_playwright_agent/profiles/sut_profiles.json');
for candidate in candidates:
  try:
    data=read_json_file(candidate)['data']; source=candidate; break
  except Exception:
    pass
if data is None: raise RuntimeError(f'Profile registry not found in {candidates}');
params=data['profiles']['timeoff']['navigation']['params'];
print(json.dumps({'source': source, 'start_url': params.get('start_url'), 'site_name': params.get('site_name'), 'output_root': params.get('output_root')}, ensure_ascii=False, indent=2))"
```

Workspace JSON helper smoke test:

```powershell
uv run python -c "from adk_playwright_agent.tools.workspace_tools import read_json_file; import json; data=read_json_file('profiles/sut_profiles.json'); print(json.dumps(sorted(data['data']['profiles'].keys()), ensure_ascii=False, indent=2))"
```

Note:

- profile JSON read/write ownership is in `workspace_tools`
- root agent system instructions apply a global routing guard that runs `profile-parameter-loading-workflow` first for navigation/test/task-generation requests
- short-prompt skill should read profile JSON via `read_json_file`
- short-prompt skill should fill missing parameters, then call the tool requested by the user

Credentials error handling smoke test:

```powershell
uv run python scripts/credentials_smoke.py
```

Vertex-backed draft case smoke test:

```powershell
uv run python scripts/intent_smoke.py
```

Compare guest and authenticated manifests:

```powershell
uv run python scripts/compare_manifests.py
```

## Optional justfile

If you use [`just`](https://github.com/casey/just), this project includes a `justfile` so the most common commands become:

```powershell
just sync
just run
just web
just lint
just manifest-test
just context-test
just crawler-test
just workflow-test
just credentials-test
just intent-test
```

## ADK Entry Point

The ADK entry point is [agent.py](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/agent.py).

It exposes:

- `root_agent`
- `get_agent_async()`

## Current Tool Groups

- Browser tools
- Workspace tools
- Generator tools
- Validation tools
- Crawler tools

## Project Design Docs

The design documents live under
[docs/adk-playwright-agent](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/docs/adk-playwright-agent).
They are intentionally consolidated into three files so the documented design
stays close to the current code:

- [ARCHITECTURE.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/docs/adk-playwright-agent/ARCHITECTURE.md)
- [TOOLING_AND_ARTIFACTS.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/docs/adk-playwright-agent/TOOLING_AND_ARTIFACTS.md)
- [ROADMAP.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/docs/adk-playwright-agent/ROADMAP.md)

## Guest Crawl Tool

The first high-level crawler tool is `crawl_site_to_manifest`. It opens a headed persistent `playwright-cli` session, performs bounded guest-only BFS over same-origin links, keeps crawl progress in `CrawlerContext`, and writes a route manifest.

Example ADK prompt:

```text
Explore http://localhost:3101 with crawl_site_to_manifest and write manifests/example_sut/route_manifest.guest.json. Use max_depth 3 and max_pages 100. Do not generate task files yet.
```

For signed-in coverage, use `crawl_authenticated_site_to_manifest` and write a separate manifest so guest and authenticated coverage can be compared before task generation.

Example ADK prompt:

```text
Explore http://localhost:3101 with crawl_authenticated_site_to_manifest and write manifests/example_sut/route_manifest.auth.json. Use credentials_system_name example_sut, credentials from passwords.txt, storage_state_path .auth/example_sut_state.json, max_depth 3, and max_pages 100. Do not generate task files yet.
```

The crawler is SUT-neutral by default. Keep `sut_profile` as `generic` unless a project deliberately provides a product-specific profile for route classification. Product-specific profiles should improve labels/classification only; they must not be required for basic crawling.

To compare two manifests:

```powershell
uv run python scripts/compare_manifests.py manifests/example_sut/route_manifest.guest.json manifests/example_sut/route_manifest.auth.json
```

## Manifest-First Workflow Tool

Use `run_manifest_first_route_workflow` when you want the full repeatable sequence:

```text
guest crawl -> guest task generation -> authenticated crawl -> auth task generation -> validation
```

Example ADK prompt:

```text
Run manifest-first route workflow for http://localhost:3102. Use site_name timeoff, credentials_system_name timeoff, output_root timeoff, storage_state_path .auth/timeoff_state.json, guest_max_depth 2, auth_max_depth 3, and max_pages 120.
```

The workflow writes separate guest/auth manifests and generated task directories under `output_root`, reports generated/skipped counts, and refuses task generation from manifests with pending or error counts.

For short prompts, prefer the `profile-parameter-loading-workflow` skill. It
reads saved defaults from `profiles/sut_profiles.json` through `read_json_file`,
resolves the SUT in skill logic, fills missing parameters, and then calls the
tool or workflow requested by the user.

At the agent-core level, this path is enforced by a global routing rule in the
root system instructions: navigation/test/task-generation requests first reuse
complete same-SUT session parameters; if parameters are missing/incomplete, the
agent must invoke `profile-parameter-loading-workflow` before directly running
workflow tools.

Design direction for profiles:

- profiles are intended to be a shared SUT parameter source across workflows,
  not a navigation-only preset
- the first implementation started with navigation routing, but the target model
  extends the same profile to action-review and direct tool flows
- profile JSON I/O should move to generic `workspace_tools` helpers

Example ADK prompt:

```text
Generate timeoff navigation tests.
```

By default, the workflow discovers the login route from the guest manifest before
starting the authenticated crawl. It looks for login/sign-in routes using URL,
label, page type, headings, actions, and password-form evidence. Recovery routes
such as forgot-password and reset-password are explicitly excluded. Pass
`login_path` only when you need to override discovery.

Authenticated crawling excludes session-ending routes such as logout, log out,
signout, sign out, and sign-off. These routes are not coverage targets because
visiting them destroys the authenticated session and reduces crawl coverage.

The same workflow is also documented as an ADK Skill at [skills/manifest-first-route-workflow/SKILL.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/skills/manifest-first-route-workflow/SKILL.md).

The short-prompt wrapper workflow is documented at [skills/profile-parameter-loading-workflow/SKILL.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/skills/profile-parameter-loading-workflow/SKILL.md).

## Task Generation

Use `generate_tasks_from_manifest` after a manifest has `pending_count: 0` and `error_count: 0`.

Example ADK prompt:

```text
Generate task JSON files from manifests/example_sut/route_manifest.auth.json into generated_tasks/example_sut/auth. Use site_name example_sut, storage_state_path .auth/example_sut_state.json, require_login true, task_id_prefix example_sut_auth. Skip unsafe routes and invalid query routes.
```

The batch generator writes `task_*.json` files, preserves the manifest navigation steps/assertions, and skips unsafe routes such as logout, delete, download, backup, export, upload, and routes with invalid query markers such as `NaN` or `undefined` unless explicitly requested.

## Vertex-Backed Draft Cases

Action discovery now targets human-reviewable, task-shaped draft cases instead
of final reviewed action tasks. This system is meant to help understand a SUT
and prepare draft cases for later refinement and downstream execution by
another web agent.

```text
route manifest
-> build_action_discovery_worklist
-> observe_task_pages_from_worklist
-> page_observations/page-*.yml
-> summarize_pages_with_vertex
-> page_summaries/page-*.summary.json
-> draft_test_ideas_with_vertex
-> page_drafts/page-*.drafts.json
-> merge_page_drafts
-> draft_backlog.json
```

`build_action_discovery_worklist` stays deterministic. It prepares canonical
routes, folds query-string variants, and skips unsafe/session-ending routes. It
must not decide what actions are available on a page.

`observe_task_pages_from_worklist` opens each canonical route with
`playwright-cli` and writes one `page-*.yml` artifact per canonical route:
the raw `playwright-cli snapshot` tree plus supporting structured evidence
(headings/text, forms, tables, snapshot paths, and route
provenance). It intentionally does not pre-classify page controls into fixed
action records.

`summarize_pages_with_vertex` reads those page artifacts and produces
`page-*.summary.json`, including a short plain-language description so a human
can skim the SUT quickly.

`draft_test_ideas_with_vertex` reads the page artifact plus optional page
summary and produces per-page draft cases. The tool normalizes each kept draft
into an AgentOccam-style task JSON payload under `drafts`, including
`gherkin`, `eval`, `require_login`, `storage_state`, and `start_url`. Fill
steps are normalized to `I fill in <field> with valid value`. Pure route-to-route
navigation drafts and simple page-entry `open` drafts are intentionally
excluded because route navigation is already covered elsewhere. For a page with
one clear primary form submission, keep one happy-path draft instead of many
invalid/empty input variants.

`merge_page_drafts` combines per-page task-shaped drafts into a single deduped
`draft_backlog.json` for human refinement. This backlog is the main output of
the current system, but it is still considered draft-quality until reviewed.

Example ADK prompt:

```text
Build an action discovery worklist from timeoff/route_manifest.auth.generic.json into timeoff/action_worklist.auth.generic.json. Use site_name timeoff and skip query-string variants.

Observe task pages from timeoff/action_worklist.auth.generic.json into timeoff/page_observations.index.json. Write observations under timeoff/page_observations. Do not classify actions or generate final task files yet.

Summarize timeoff/page_observations.index.json into timeoff/page_summaries.index.json and write summaries under timeoff/page_summaries.

Draft page-level test ideas from timeoff/page_observations.index.json into timeoff/page_drafts.index.json. Use timeoff/page_summaries.index.json to help with page understanding.

Merge timeoff/page_drafts.index.json into timeoff/draft_backlog.json. Stop after the draft backlog is written.
```

For the repeatable workflow wrapper, use:

```text
Run the action-review-task-workflow skill for timeoff.
Use worklist_path timeoff/action_worklist.auth.generic.json, output_root timeoff, storage_state_path .auth/timeoff_state.json, and start_url http://localhost:3102.
First observe pages, then summarize them, then draft page-level cases, and finally merge to draft_backlog.json.
```

Query-string routes such as
`/calendar/teamview?department=1&date=2026-03` should be folded into
`/calendar/teamview` by the worklist and not observed or generated as a separate
draft page.

## Context Memory

The scaffold includes `CrawlerContext` primitives for separating:

- working memory: compact current page state and the most recent 3 operation results
- task state: deterministic crawl progress such as visited paths, pending paths, and skipped routes
- long-term memory: stable goal, credential references without raw passwords, storage state paths, and blocked actions

Use `build_context_pack()` before asking the model to reason about crawl progress. It compacts large route/link lists when the estimated context size crosses the configured threshold.

## Next Implementation Slice

Recommended next steps:

1. Run the guest-only BFS crawl against the SUT home page and inspect the guest manifest.
2. Run the authenticated BFS crawl and inspect the authenticated manifest.
3. Compare guest and authenticated manifests for duplicate routes and login-only routes.
4. Split large authenticated coverage by role, route prefix, or feature area instead of only increasing `max_pages`.
5. Add project-specific observation and prompt refinements once the manifests are stable.
6. Improve draft backlog quality after the manifest-first workflow is stable.
