# ADK Playwright Agent Architecture

## Project Positioning

This project is a generic SUT exploration and test-ideation system built on
Google ADK plus `playwright-cli`.

Its current job is:

- crawl guest and authenticated routes
- generate route-level navigation tasks
- revisit canonical routes as live pages
- summarize each page with Vertex
- produce human-reviewable, task-shaped draft cases

Its current job is not:

- generate fully reviewed final action tasks
- fully execute create/edit/delete workflows
- optimize for one SUT such as TimeOff, NodeBB, or Keystone

The draft backlog produced here is intended for later human refinement and
downstream execution by another web agent.

## Current System Boundaries

The project has two major output tracks.

### 1. Route Coverage Track

This track is deterministic and manifest-driven.

```text
start_url
-> crawl_site_to_manifest / crawl_authenticated_site_to_manifest
-> route_manifest.*.json
-> generate_tasks_from_manifest
-> task_*.json
-> validate_task_directory
```

Purpose:

- discover route coverage
- preserve navigation steps from the configured home page
- generate stable route-level navigation tasks

### 2. Draft Case Track

This track is browser-backed and Vertex-assisted.

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

Purpose:

- understand each canonical page as a live UI
- summarize page intent in plain language
- propose evidence-backed task-shaped draft cases for human review

## Core Design Decisions

### Generic Across SUTs

The crawler, worklist builder, observation format, and draft pipeline are
intentionally generic. Product-specific behavior belongs in:

- manifests
- generated outputs
- optional profile defaults

It should not be baked into crawler defaults or prompt assumptions.

### Canonical Route First

Action discovery does not open every URL variant. Query-string variants are
folded into one canonical path for page observation and draft generation.

Example:

```text
/calendar/teamview?department=1&date=2026-03
/calendar/teamview
```

Only `/calendar/teamview` should be observed and drafted.

### Observation Before Interpretation

The system first records page evidence, then asks Vertex to summarize and draft.
It does not pre-author final actions from static route metadata.

### Stop at Draft Backlog

This repo currently stops at:

- route navigation tasks
- page summaries
- page draft cases shaped like downstream task JSON
- consolidated draft backlog

It does not claim those draft cases are final executable create/edit/delete
workflows. They are intentionally shaped for downstream web agents, then left
for human refinement.

## Main Runtime Pieces

### Root ADK Agent

Defined in [agent.py](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/agent.py).

Responsibilities:

- choose the right workflow
- expose browser/workspace/crawler/generator/draft tools
- load workflow skills

### Skills

Current workflow skills:

- [skills/manifest-first-route-workflow/SKILL.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/skills/manifest-first-route-workflow/SKILL.md)
- [skills/action-review-task-workflow/SKILL.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/skills/action-review-task-workflow/SKILL.md)
- [skills/profile-parameter-loading-workflow/SKILL.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/skills/profile-parameter-loading-workflow/SKILL.md)

### Browser Adapter

`adapters/playwright_cli.py` wraps `playwright-cli` and returns structured
results for:

- open
- goto
- snapshot
- click
- fill
- eval
- state save/load
- close

### Vertex Adapter

`adapters/vertex_genai.py` handles page summary and page draft requests through
Vertex AI. It now includes retry/backoff for `429 RESOURCE_EXHAUSTED` on the
custom draft/summary path.

## Observation Model

`observe_task_pages_from_worklist` writes one `page-*.yml` per canonical route.

The current page artifact keeps:

- route provenance
- baseline URL/title/headings
- raw `playwright-cli snapshot` content in `page_snapshot`
- visible text
- visible forms
- table summaries
- errors

It intentionally does not keep `observed_controls` anymore.

This means the primary page representation is now the snapshot tree plus a small
amount of supporting structured evidence.

## Draft Case Policy

The retained task-shaped draft backlog should favor:

- `create`
- `edit`
- `delete`
- `filter`
- `search`

The current pipeline intentionally filters out:

- `navigate`
- simple `open`
- `export`
- `import`
- `auth_session`
- `unknown`

For pages with one clear primary form submission, the pipeline tries to keep one
happy-path draft instead of many invalid/empty/minimal input variants.

## Profiles and Short Prompts

The project supports profile-backed parameter reuse through
`profiles/sut_profiles.json` plus skill routing.

The intended model is:

1. reuse complete same-SUT session parameters when possible
2. otherwise resolve profile defaults
3. then call the requested workflow/tool with explicit merged parameters

## Current Constraints

- route generation is more mature than page draft quality
- Vertex draft quality still depends heavily on page artifact quality and prompt tuning
- ADK's own LLM path may still hit Vertex shared-capacity limits independently of the custom Vertex adapter
- downstream execution handoff is intentionally lightweight and not yet deeply integrated

## Source of Truth

For operator usage and environment setup, start with the repo
[README.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/README.md).

For tool contracts and artifacts, use
[TOOLING_AND_ARTIFACTS.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/docs/adk-playwright-agent/TOOLING_AND_ARTIFACTS.md).

For planned follow-up work, use
[ROADMAP.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/docs/adk-playwright-agent/ROADMAP.md).
