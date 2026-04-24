# Parameter Profiles and Short Prompt Invocation

## Problem Statement

Operators currently repeat many workflow parameters in every prompt, such as:

- start URL
- output root
- credentials system name
- credentials path
- storage state path
- crawl depth and limits

This makes normal operations verbose and error-prone, especially for recurring
targets such as timeoff.

The first implementation focused on navigation workflow defaults. The target
design in this document is broader: one SUT profile should be reusable across
all major workflows and tool runs, not only `run_manifest_first_route_workflow`.

## Target Operator Experience

For common workflows, operators should be able to use one short prompt:

```text
Generate timeoff navigation tests.
```

The agent should resolve saved defaults and call the correct workflow tool
without requiring repeated parameter lists.

The same profile should also support longer, explicit requests for other tasks,
for example page-summary / draft-case generation or direct crawl tools.

## Design Overview

Use a profile-backed invocation model with three layers:

1. Workflow intent detection from natural language
2. SUT profile lookup for saved parameters
3. Deterministic tool invocation with explicit override precedence

This keeps operator prompts short while preserving reproducibility.

For cross-workflow support, profile data should be treated as a shared parameter
registry with workflow-specific overlays.

## Agent-Core Interceptor + Skill Routing

Use a two-layer control model:

1. root system instructions enforce a global session-first routing interceptor
2. skill contracts implement parsing, merge, and tool selection details

Recommended path for short prompts:

1. root system instructions check whether same-SUT session parameters are complete
2. if parameters are missing/incomplete, route to `profile-parameter-loading-workflow`
3. parse prompt text inside skill routing logic
4. load profile JSON from `profiles/sut_profiles.json` and optional
  `.adk/sut_profiles.local.json`
5. merge SUT profile context and optional prompt overrides
6. resolve target workflow context from profile
7. execute the mapped workflow/tool with resolved parameters

This keeps routing robust at the agent-core layer while still keeping detailed
behavior versioned in skill docs and tools.

### Ownership Model

- `workspace_tools` owns generic JSON file operations (read/write/merge helpers)
- skill/workflow layers own intent parsing and parameter resolution policy
- no dedicated profile resolver module is required when skill-level routing uses JSON tools directly

Current implementation slice:

- generic `read_json_file`, `write_json_file`, and `merge_json_files` helpers
  are available in `workspace_tools`
- short-prompt skill routing reads registry JSON through `workspace_tools.read_json_file`

## SUT Profile Registry

Define a profile registry file for reusable SUT defaults.

Suggested location:

- project-shared defaults: `profiles/sut_profiles.json`
- local override (not required in repo): `.adk/sut_profiles.local.json`

Suggested shape:

```json
{
  "version": "1.0",
  "profiles": {
    "timeoff": {
      "aliases": ["timeoff", "to"],
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
            "validate_outputs": true,
            "include_home": true,
            "include_unsafe_routes": false,
            "skip_invalid_query_routes": true
          }
        },
        "action_tasks": {
          "tool": "action-review-task-workflow",
          "params": {
            "build_worklist": true,
            "observe_pages": true,
            "summarize_pages": true,
            "draft_page_cases": true,
            "merge_backlog": true
          }
        }
      },
      "tools": {
        "crawl_site_to_manifest": {
          "params": {
            "same_origin_only": true
          }
        },
        "observe_task_pages_from_worklist": {
          "params": {
            "max_controls_per_route": 80
          }
        }
      }
    }
  }
}
```

Backwards compatibility policy:

- existing `navigation`-only shape remains valid during transition
- resolver should map old shape to `workflows.navigation`
- new shape is preferred for all new profiles

Migration note:

- current implementation uses skill/workflow routing plus `workspace_tools` JSON helpers
- avoid introducing profile-specific resolver modules unless runtime behavior proves it necessary

Notes:

- store credential file references, never raw passwords
- keep profile values explicit and serializable
- keep profile keys generic across SUTs
- keep file paths workspace-relative to the active agent workspace root
- for this repo, prefer `passwords.txt` when the workspace root is the
  `adk_playwright_agent` folder

## Parameter Resolution for All Tasks

When the user runs any workflow/tool, resolve parameters in this order:

1. explicit prompt overrides
2. explicit runtime context (session-level values from prior clarification)
3. workflow-specific profile defaults (`workflows.<workflow>.params`)
4. tool-specific profile defaults (`tools.<tool>.params`)
5. shared profile defaults (`shared.target`, `shared.auth`, `shared.limits`, ...)
6. workflow/tool safe defaults

This allows concise prompts while still supporting one-off overrides.

## Intent Routing Rules

For short prompts, route to the intended workflow, then resolve profile context.

Examples that should map correctly:

- generate timeoff navigation tests
- run timeoff route navigation generation
- 幫我生成 timeoff 的 navigation 測試
- 幫我對 timeoff 生成 draft case backlog

Expected tool target:

- skill wrapper for the detected workflow
- then shared profile resolver
- then mapped workflow tool

Expected output summary (workflow dependent):

- resolved profile id and parameter source map
- generated/skipped counts
- validation issues
- output paths

## Minimal Clarification Policy

If profile lookup fails, ask one targeted question only.

Examples:

- unknown SUT name: ask which profile to use
- missing required start URL: ask for URL once, then persist to profile/session
- missing workflow-required input (for example `intents_path`): ask only that field

Do not ask for parameters already available in profile defaults.

## State and Memory Integration

Cache run-time profile resolution in session state so repeated commands in the
same session need fewer lookups.

Recommended session keys:

- `target.sut_id`
- `target.profile_id`
- `target.workflow_type`
- `target.tool_name`
- `target.profile_source`
- `target.last_resolved_params`

This does not replace the canonical profile file; it only avoids repeated
resolution work within a run.

## Skill Alignment

Profile-backed invocation should remain compatible with all skillized workflows.

For each skill intent:

- resolve SUT and workflow intent
- resolve cross-workflow profile context
- call mapped workflow tool with merged params

The profile system is an operator experience and consistency layer across all
workflows, not a navigation-only helper.

Implementation direction for this section:

- avoid introducing workflow-specific JSON helpers in `workspace_tools`
- keep `workspace_tools` generic (JSON I/O only)
- keep workflow semantics in skill/workflow resolver code

## Acceptance Criteria

This design is successful when all are true:

- operator can invoke common runs with short prompts without listing repeated params
- profile defaults are reusable across navigation, draft-case generation, and direct tool runs
- resolved params are deterministic and auditable
- missing profile data causes at most one focused clarification question
- final workflow output matches manually parameterized runs
- no raw secret values are stored in profile files or session state

## Rollout Plan

1. Define cross-workflow profile schema and compatibility policy in docs.
2. Add generic JSON read/write helpers to `workspace_tools` for profile files.
3. Keep profile resolution in skill/workflow layers with JSON-tool-based lookup.
4. Keep the skill contract generic so it can feed parameters into multiple workflows/tools.
5. Add fallback path handling for profile registry lookup under different workspace roots.
6. Add smoke tests for cross-workflow parameter resolution and short prompt routing.
