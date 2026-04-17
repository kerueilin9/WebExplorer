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

Example values:

- `http://localhost:3101/`
- `D:/Ker/Desktop/Document/other/GUI_test/manifests/example_sut`
- `task_json`

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
