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
- `GOOGLE_API_KEY`

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

Action intent extraction smoke test:

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

The design and implementation planning documents live under
[docs/adk-playwright-agent](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/docs/adk-playwright-agent).
They are kept inside this repository so code, tool schemas, and operator
workflow documentation can evolve together.

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

## Action Intent Extraction

The current `extract_action_intents_from_manifest` helper is a static prototype.
The target design is browser-backed action discovery: build a canonical route
worklist from a stable manifest, skip or fold query-string variants, open each
canonical route with `playwright-cli`, inspect the live UI, and generate action
tasks only from observed safe UI evidence.

The first implemented steps are `build_action_discovery_worklist`, which prepares
the canonical route list, and `discover_page_actions_from_worklist`, which opens
each canonical route and writes per-route browser evidence plus action-intent
drafts. Browser-backed discovery folds query variants, skips auth redirects,
deduplicates repeated global controls across routes, and suppresses controls
already covered by stronger route/form evidence.

`generate_action_tasks_from_intents` converts accepted browser-backed intents
into conservative action task JSON files. Create/edit tasks open the workflow
entrypoint and explicitly stop before submit/save/commit actions. Low-value
labels such as numeric-only controls or raw path labels are skipped.

For higher semantic quality, add an LLM review pass between discovery and task
generation. `prepare_action_intent_review_packets` writes route-scoped packets
containing baseline evidence, forms, controls, and heuristic candidates. The ADK
agent reviews those packets as a generic SUT reviewer, then
`write_reviewed_action_intents` writes only constrained updates to existing
intent IDs. This keeps LLM judgment in the loop without letting it invent
unobserved routes or controls.

Reviewed intents can provide executable `workflow_steps`, `test_data`,
`success_evidence`, and `commit_policy`. When those fields are present,
generated create/edit tasks can fill and submit real workflows. Without those
reviewed fields, create/edit tasks remain conservative and stop before commit.

Example ADK prompt:

```text
Build an action discovery worklist from timeoff/route_manifest.auth.generic.json into timeoff/action_worklist.auth.generic.json. Use site_name timeoff and skip query-string variants.

Run browser-backed page action discovery from timeoff/action_worklist.auth.generic.json into timeoff/action_intents.browser.auth.generic.json. Write evidence under timeoff/action_discovery. Do not submit forms or generate final action tasks yet.

Prepare action intent review packets from timeoff/action_intents.browser.auth.generic.json into timeoff/action_review_packets. Review the packets as a generic SUT action reviewer, then write reviewed intents to timeoff/action_intents.reviewed.auth.generic.json.

Generate action tasks from timeoff/action_intents.reviewed.auth.generic.json into timeoff/generated_tasks/actions. Use site_name timeoff, storage_state_path .auth/timeoff_state.json, and clear_existing true.
```

For the repeatable workflow wrapper, use:

```text
Run the action-review-task-workflow skill for timeoff/action_intents.browser.auth.generic.json.
Use site_name timeoff, output_root timeoff, storage_state_path .auth/timeoff_state.json, and start_url http://localhost:3102.
First create review packets and stop for review.
```

The browser-backed phase writes per-route evidence plus `action_intents.json`
style metadata, then action task files can be generated from accepted intents.
Query-string routes such as
`/calendar/teamview?department=1&date=2026-03` should be folded into
`/calendar/teamview` and not explored or generated as separate action tasks.
High-risk candidates such as delete, import, upload, export, approve, and reject
remain skipped by default and reported in `skipped_candidates`.
Repeated global controls such as a persistent top-nav `New` button are retained
once and reported as `global_duplicate` on later routes.

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
5. Add project-specific output generators once the manifests are stable.
6. Add task generation after the manifest-first workflow is stable.
