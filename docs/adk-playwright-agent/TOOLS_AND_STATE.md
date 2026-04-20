# Tool and State Design

## Tool Design Principles

The agent should call narrow, typed tools instead of arbitrary shell commands.

Goals:

- reduce prompt ambiguity
- keep actions auditable
- make failures easier to classify
- simplify later migration to MCP

## Browser Toolset

These tools wrap `playwright-cli` and present a structured interface to ADK.

### `open_browser`

Purpose:

- create or resume a named browser session

Inputs:

- `base_url: str`
- `session_name: str`
- `headed: bool = True`
- `persistent: bool = True`

Returns:

- `session_name`
- `resolved_url`
- `page_title`
- `opened: bool`

Notes:

- should use `playwright-cli -s=<session> open <url> --headed --persistent`

### `goto`

Purpose:

- navigate the current browser session to a URL

Inputs:

- `session_name: str`
- `url: str`

Returns:

- `url`
- `title`

### `snapshot`

Purpose:

- capture the current UI structure for reasoning and locator discovery

Inputs:

- `session_name: str`
- `depth: int | None = None`

Returns:

- `url`
- `title`
- `snapshot_path`
- optional `ref_summary`

### `click`

Purpose:

- click a snapshot reference or a locator

Inputs:

- `session_name: str`
- `target: str`

Returns:

- `url`
- `title`
- `clicked: bool`

### `fill`

Purpose:

- fill a field using a locator

Inputs:

- `session_name: str`
- `target: str`
- `text: str`
- `submit: bool = False`

Returns:

- `filled: bool`
- `target`

### `press_key`

Purpose:

- press keyboard keys for navigation and form submission

Inputs:

- `session_name: str`
- `key: str`

Returns:

- `pressed: bool`

### `eval_js`

Purpose:

- query structured page details when snapshots are insufficient

Inputs:

- `session_name: str`
- `script: str`

Returns:

- `result`

Usage examples:

- current URL
- title
- visible forms
- input placeholders
- same-origin links

### `save_storage_state`

Purpose:

- save login state for later reuse

Inputs:

- `session_name: str`
- `path: str`

Returns:

- `path`
- `saved: bool`

### `close_browser`

Purpose:

- close a named browser session

Inputs:

- `session_name: str`

Returns:

- `closed: bool`

## Workspace Toolset

### `list_files`

Purpose:

- inspect a workspace or output directory

Inputs:

- `path: str`
- `glob: str | None = None`

Returns:

- `files: list[str]`

### `read_text_file`

Purpose:

- read test templates, credentials notes, or existing tasks

Inputs:

- `path: str`

Returns:

- `content: str`

### `write_text_file`

Purpose:

- create generated task or test files

Inputs:

- `path: str`
- `content: str`

Returns:

- `written: bool`
- `path`

### `edit_text_file`

Purpose:

- patch existing generated output without rewriting everything

Inputs:

- `path: str`
- `instruction: str`

Returns:

- `edited: bool`

## Optional Generator Toolset

This can be kept internal to the agent at first, but it can also be exposed as tools later.

### `generate_navigation_tasks`

Inputs:

- `route_manifest`
- `output_dir`
- `require_login`
- `storage_state_path`

Returns:

- `generated_files`

### `build_action_discovery_worklist`

Inputs:

- `route_manifest`
- `skip_query_variants: bool = true`

Returns:

- `worklist_path`
- `canonical_route_count`
- `folded_variant_count`

### `discover_page_actions`

Inputs:

- `action_worklist`
- `session_name`
- `max_actions_per_route`
- `max_safe_clicks_per_route`
- `allowed_action_types`

Returns:

- `action_catalog_path`
- `evidence_dir`
- `intent_count`
- `skipped_intents`

Notes:

- this tool or subflow must open each canonical route with `playwright-cli`
- final action tasks must be based on live snapshot/DOM evidence, not static manifest scanning alone
- query-string variants should be folded into their base path and skipped for separate action discovery by default

### `generate_action_tasks`

Inputs:

- `action_catalog`
- `output_dir`
- `require_login`
- `storage_state_path`

Returns:

- `generated_files`

### `generate_playwright_test`

Inputs:

- `route_metadata`
- `output_dir`
- `project_style`

Returns:

- `output_path`

## Skill Packaging for Long Workflows

Use ADK Skills to package long multi-step operations as reusable workflow units.
The official ADK documentation marks Skills as experimental and supported in
ADK Python v1.25.0+, so keep this wiring behind the stable 1.x dependency line
and re-check `https://adk.dev/skills/` before API changes.

Recommended directory structure:

```text
my_agent/
  agent.py
  skills/
    manifest_first_route_workflow/
      SKILL.md
      references/
        ROUTE_WORKFLOW.md
        VALIDATION_RULES.md
      assets/
        prompt_templates.md
      scripts/
        compare_manifests.py
```

Guidelines:

- keep `SKILL.md` focused on ordered execution steps and guardrails
- store long parameter defaults and examples in `references/`
- keep project-specific templates in `assets/`
- only include scripts that are deterministic and auditable

Suggested skill responsibilities for this project:

- run guest crawl manifest generation
- run authenticated crawl manifest generation
- generate route-level navigation tasks from each manifest
- validate guest/auth task directories and report summary metrics

Agent wiring recommendation:

- load skills with `load_skill_from_dir(...)`
- import `skill_toolset` with `from google.adk.tools import skill_toolset`
- attach skill bundles via `skill_toolset.SkillToolset(...)`
- keep skill invocation explicit in root instructions to avoid accidental overuse

## Confirmation Rules

The following actions should require ADK confirmation or policy checks before execution:

- clicking destructive actions such as delete, disable, remove, purge
- submitting changes in admin pages
- editing user data
- writing outside the configured workspace
- executing arbitrary commands beyond approved wrappers

This maps well to ADK's tool confirmation support.

## ADK State Schema

These keys should live in `session.state`.

### Site Configuration

- `target.base_url`
- `target.output_dir`
- `target.mode`
- `target.sut_id`
- `target.profile_id`
- `target.workflow_type`
- `target.tool_name`
- `target.profile_source`
- `target.last_resolved_params`

Example values:

- `http://localhost:3101/`
- `D:/Ker/Desktop/Document/other/GUI_test/manifests/example_sut`
- `task_json`
- `timeoff`
- `timeoff.navigation.default`
- `manifest_first_navigation`
- `run_manifest_first_route_workflow`

### Browser Session

- `browser.session_name`
- `browser.headed`
- `browser.persistent`
- `browser.current_url`
- `browser.current_title`
- `browser.storage_state_path`

### Crawl Progress

- `crawl.start_url`
- `crawl.visited_paths`
- `crawl.pending_paths`
- `crawl.discovered_links`
- `crawl.discovered_forms`
- `crawl.requires_login`
- `crawl.phase`

### Action Discovery

- `action.discovery_enabled`
- `action.pending_route_ids`
- `action.canonical_worklist_path`
- `action.folded_query_variants`
- `action.current_route_id`
- `action.current_canonical_path`
- `action.discovered_intents`
- `action.evidence_dir`
- `action.intent_catalog_path`
- `action.generated_task_files`

### Skill Run State

- `skill.active_name`
- `skill.run_id`
- `skill.run_phase`
- `skill.last_summary`
- `skill.generated_artifacts`

Recommended phases:

- `discover_guest`
- `login`
- `discover_member`
- `discover_admin`
- `generate_output`
- `validate_output`

### Auth

- `auth.credentials_file`
- `auth.username`
- `auth.password_source`
- `auth.logged_in`

Do not store the raw password in long-lived state if it can be avoided. Prefer:

- reading from a file on demand
- storing only the credentials file path or username

### Output Tracking

- `output.generated_files`
- `output.route_manifest_path`
- `output.format`
- `output.validation_summary`

### Policy

- `policy.allow_admin_submit`
- `policy.allow_destructive_clicks`
- `policy.allow_file_write`

## Parameter Profile Registry

To reduce repetitive prompts, add a profile registry for reusable SUT
parameters across all workflows and direct tool runs.

Suggested files:

- shared defaults: `profiles/sut_profiles.json`
- local override: `.adk/sut_profiles.local.json`

Suggested profile shape:

```json
{
  "version": "1.0",
  "profiles": {
    "timeoff": {
      "aliases": ["timeoff"],
      "shared": {
        "target": {
          "start_url": "http://localhost:3102",
          "site_name": "timeoff",
          "output_root": "timeoff",
          "sut_profile": "generic"
        },
        "auth": {
          "credentials_system_name": "timeoff",
          "credentials_path": "passwords.txt",
          "storage_state_path": ".auth/timeoff_state.json"
        },
        "limits": {
          "guest_max_depth": 2,
          "auth_max_depth": 3,
          "max_pages": 120
        }
      },
      "workflows": {
        "navigation": {
          "tool": "run_manifest_first_route_workflow",
          "params": {
            "run_guest": true,
            "run_authenticated": true,
            "generate_guest_tasks": true,
            "generate_auth_tasks": true,
            "validate_outputs": true
          }
        },
        "action_review": {
          "tool": "run_action_review_task_workflow",
          "params": {
            "require_review": true,
            "generate_tasks": true,
            "validate_outputs": true
          }
        }
      },
      "tools": {
        "crawl_site_to_manifest": {
          "params": {
            "same_origin_only": true
          }
        }
      }
    }
  }
}
```

Rules:

- store only credential references and paths, never raw secret values
- keep values serializable and auditable
- separate shared defaults from developer-local overrides
- keep path fields workspace-relative to the active agent workspace root
- keep a compatibility path for existing `navigation`-only profiles during migration

## Workspace JSON Tool Ownership

Use `workspace_tools` as the generic JSON data access layer.

Recommended helpers:

- `read_json_file(path)`
- `write_json_file(path, data, overwrite=true)`
- optional: `merge_json_files(base_path, override_path)` for local overrides

Current implementation slice:

- `read_json_file(path)` implemented
- `write_json_file(path, data, overwrite=true)` implemented
- `merge_json_files(base_path, override_path, output_path?)` implemented

Scope boundaries:

- `workspace_tools`: file I/O only, no workflow semantics
- skill/workflow resolver: intent parsing, profile selection, parameter merge rules
- keep profile resolution in skill/workflow routing, not in a dedicated profile wrapper module

## Short Prompt Resolution Policy

For prompts such as Generate timeoff navigation tests:

0. enforce agent-core routing guard in system instructions: reuse complete same-SUT session parameters first
1. detect workflow intent (navigation)
2. parse prompt text in skill/workflow routing logic
3. if required parameters are missing/incomplete, load profile JSON through `workspace_tools` helpers
4. resolve SUT profile (timeoff)
5. merge parameters using precedence:
  - prompt overrides
  - workflow-specific profile defaults
  - tool-specific profile defaults
  - shared profile defaults
  - workflow safe defaults
6. trigger the mapped skill wrapper (`profile-parameter-loading-workflow`)
7. let the skill call the workflow/tool with resolved params

If required profile fields are missing, ask one targeted clarification question
and reuse the answer for subsequent runs.

Routing uses both layers:

- root system instructions: global pre-routing guard that reuses session context first and triggers profile loading on missing/incomplete fields
- skill/tool contracts: detailed parsing, merge precedence, and tool selection rules

For non-navigation workflows (for example action-review), use the same profile
resolution contract with the corresponding workflow key.

## Route Metadata Shape

Each discovered route should be normalized into one serializable structure.

Suggested shape:

```json
{
  "label": "Project Alpha",
  "path": "/projects/42",
  "full_url": "http://localhost:3101/projects/42",
  "source": "home",
  "require_login": false,
  "page_type": "detail",
  "navigation_steps": [
    "I open the configured home page",
    "I click the \"Project Alpha\" link from the home page"
  ],
  "assertions": [
    "The browser URL should include \"/projects/42\"",
    "The page title or primary heading should show \"Project Alpha\""
  ]
}
```

## Browser-Backed Action Evidence Shape

Each action-discovery route should produce an evidence record before final task
generation.

Suggested shape:

```json
{
  "route_id": "timeoff_authenticated_page_calendar_teamview",
  "canonical_path": "/calendar/teamview",
  "selected_url": "http://localhost:3102/calendar/teamview",
  "folded_variants": [
    "/calendar/teamview?department=1&date=2026-03"
  ],
  "baseline": {
    "title": "Team view | TimeOff",
    "headings": ["Team view"],
    "snapshot_path": "timeoff/action_discovery/snapshots/teamview__baseline.json"
  },
  "observed_controls": [
    {"label": "Department", "kind": "filter", "safe": true},
    {"label": "Export", "kind": "download", "safe": false}
  ],
  "safe_clicks": [],
  "blocked_actions": [
    {"label": "Export", "reason": "download_blocked"}
  ],
  "task_drafts": []
}
```

## Action Intent Metadata Shape

After route discovery, each route may produce zero or more action intents.

Suggested shape:

```json
{
  "intent_id": "timeoff_create_employee_users_add",
  "route_id": "timeoff_authenticated_account_users_add",
  "intent_type": "create",
  "entity": "employee",
  "entry_path": "/users/add",
  "require_login": true,
  "input_fields": [
    "firstname",
    "lastname",
    "email"
  ],
  "submit_control": "Create",
  "success_evidence": [
    "Employee list shows the new employee",
    "Success toast or confirmation message"
  ],
  "safety_level": "safe_with_confirmation"
}
```

## Action Task Metadata Shape

Suggested task shape for a workflow case:

```json
{
  "task_id": "timeoff_auth_create_employee",
  "gherkin": {
    "feature": "timeoff Employee Management",
    "scenario": "Create Employee",
    "given": ["I am logged in to the site"],
    "when": [
      "I open the configured home page",
      "I navigate to /users/add",
      "I fill required employee fields",
      "I submit the employee form"
    ],
    "then": [
      "The employee list should include the new employee"
    ]
  }
}
```

## Failure Classification

The browser tools should return structured errors that the ADK agent can reason about.

Suggested categories:

- `locator_not_found`
- `navigation_failed`
- `login_failed`
- `action_discovery_failed`
- `intent_classification_failed`
- `permission_blocked`
- `unexpected_dialog`
- `write_failed`
- `validation_failed`

This matters because the agent should react differently to each category.

Example:

- `locator_not_found`: retry with snapshot or alternate locator
- `login_failed`: stop and report credentials issue
- `validation_failed`: keep output but mark route as partial
