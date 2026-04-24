# Tooling And Artifacts

## Current Tool Surface

The root agent currently exposes four practical groups of tools.

### Browser Tools

Main wrappers over `playwright-cli`:

- `open_browser`
- `goto`
- `snapshot`
- `click`
- `fill`
- `press_key`
- `eval_js`
- `save_storage_state`
- `close_browser`

Use these when the agent needs live browser interaction.

### Crawl and Route Tools

- `crawl_site_to_manifest`
- `crawl_authenticated_site_to_manifest`
- `write_route_manifest`
- `generate_task_file`
- `generate_tasks_from_manifest`
- `validate_task_file`
- `validate_task_directory`

Use these for route coverage, manifest writing, route task generation, and validation.

### Draft Discovery Tools

- `build_action_discovery_worklist`
- `observe_task_pages_from_worklist`
- `summarize_pages_with_vertex`
- `draft_test_ideas_with_vertex`
- `merge_page_drafts`
- `consolidate_task_drafts_to_backlog`

Use these for the page-summary and draft-backlog path.

### Workspace Tools

- `list_files`
- `read_text_file`
- `read_json_file`
- `write_text_file`
- `write_json_file`
- `merge_json_files`

Use these for local file access and profile JSON handling.

## Workflow Contracts

### Manifest-First Route Workflow

Tool:

- `run_manifest_first_route_workflow`

Primary output:

- guest/auth manifests
- guest/auth route task directories
- validation summary

### Action Review Task Workflow

Skill:

- [skills/action-review-task-workflow/SKILL.md](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/skills/action-review-task-workflow/SKILL.md)

Primary output:

- page observations
- page summaries
- page drafts
- draft backlog

This workflow stops at `draft_backlog.json`.

## Artifact Layout

Typical output layout for the draft-case path:

```text
<output_root>/
  action_worklist*.json
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

Typical output layout for the route-coverage path:

```text
<output_root>/
  route_manifest.guest*.json
  route_manifest.auth*.json
  generated_tasks/
    guest/
    auth/
```

## Artifact Schemas

### Route Manifest

Purpose:

- stable route inventory
- source for route-level navigation tasks

Important fields:

- `start_url`
- `base_origin`
- `routes`
- `skipped_routes`
- `summary`

### Action Discovery Worklist

Purpose:

- canonical route list for page observation

Important fields:

- `site_name`
- `start_url`
- `routes`
- `summary.canonical_route_count`
- `summary.folded_variant_count`

Each route record should keep:

- `canonical_path`
- `selected_url`
- `folded_variants`
- `phase`
- `require_login`

### Page Observation

Purpose:

- evidence-first page artifact for Vertex summary and draft generation

Current shape:

```json
{
  "page_id": "page-001",
  "route": {
    "canonical_path": "/owners/new"
  },
  "baseline": {
    "url": "http://localhost:3001/owners/new",
    "title": "PetClinic :: a Spring Framework demonstration",
    "headings": ["Owner"],
    "snapshot_path": "",
    "snapshot_ok": true
  },
  "page_snapshot": {
    "source": "playwright-cli snapshot",
    "path": "",
    "content": "- generic [active] [ref=e1]: ..."
  },
  "visible_text": "Owner First Name Last Name ...",
  "forms": [
    {
      "tag": "input",
      "type": "text",
      "name": "firstName",
      "label": "firstName"
    }
  ],
  "tables": [],
  "errors": []
}
```

Notes:

- `page_snapshot.content` is the primary page representation
- `forms` and `visible_text` are supporting evidence
- `observed_controls` is no longer part of the observation artifact

### Page Summary

Purpose:

- quick human understanding of the page

Important fields:

- `plain_language_summary`
- `page_purpose`
- `main_entities`
- `key_forms`
- `key_actions`
- `likely_user_goals`
- `risk_notes`
- `evidence`

### Page Drafts

Purpose:

- page-level candidate cases before dedupe

Important fields:

- `page_id`
- `route`
- `plain_language_summary`
- `drafts`

Each draft should keep:

- `draft_id`
- `title`
- `goal`
- `category`
- `priority`
- `risk`
- `rough_steps`
- `evidence`
- `notes_for_human`

### Draft Backlog

Purpose:

- merged and deduped inventory for human refinement

Important fields:

- `summary`
- `tasks`
- `skipped_drafts`

This is the main downstream handoff artifact for the current page-draft system.

## Draft Retention Policy

The current pipeline keeps and prioritizes:

- `create`
- `edit`
- `delete`
- `filter`
- `search`
- `export`
- `import`
- `auth_session`

The current pipeline excludes:

- `navigate`
- simple `open`

For pages with one clear primary form workflow, draft reduction favors one
happy-path variant instead of many invalid/empty/minimal variations.

## Profiles and Short Prompt Support

The shared profile registry is:

- `profiles/sut_profiles.json`

Optional local override:

- `.adk/sut_profiles.local.json`

Profile responsibilities:

- save shared target/auth/limit defaults
- support short prompts
- avoid repeating common SUT parameters

Current ownership split:

- `workspace_tools`: JSON file I/O
- skill/workflow layer: profile lookup, merge precedence, routing

## Environment Notes

Important environment variables include:

- `ADK_MODEL`
- `AGENT_WORKSPACE_ROOT`
- `PLAYWRIGHT_CLI_BIN`
- `DEFAULT_CREDENTIALS_FILE`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_GENAI_USE_VERTEXAI`
- `VERTEX_MODEL`

Vertex-backed page summary and draft generation go through
`adapters/vertex_genai.py`. That adapter now supports retry/backoff for
`429 RESOURCE_EXHAUSTED`.

## What Is Intentionally Not Here

This project does not currently maintain a documented contract for:

- final executable create/edit action-task JSON
- `reviewed_intents` files
- static `action_intents` extraction
- `observed_controls`-driven action schemas

Those were part of earlier experiments and are no longer the active design.
