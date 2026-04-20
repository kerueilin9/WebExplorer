---
name: profile-parameter-loading-workflow
description: Resolve reusable SUT parameters from profile JSON, then apply them to the tool or workflow requested by the user.
compatibility: google-adk>=1.31.0,<2.0
---

# Profile Parameter Loading Skill (Simple JSON-Tool Mode)

Use this skill when user input is short or missing repeated parameters.

Examples:

```text
Generate timeoff navigation tests.
```

```text
幫我產出 timeoff nevigation 的task
```

```text
Run action review for timeoff intents.
```

This skill is generic:

- load profile data with JSON tools
- resolve SUT from profile id or aliases
- fill missing parameters for the requested tool/workflow

No dedicated profile resolver tool is required.

## Required Inputs

- `prompt_text`: raw prompt text.

## Optional Inputs

- `sut`: explicit SUT override when prompt text is ambiguous.
- `overrides_json`: one-off overrides JSON object string.
- `profile_path`: defaults to `profiles/sut_profiles.json`.

If default path lookup fails, try fallback path:

- `adk_playwright_agent/profiles/sut_profiles.json`

## Tool Sequence (Required)

1. Load profile registry JSON with `read_json_file`:
   - first try `profile_path` (default: `profiles/sut_profiles.json`)
   - if not found, retry `adk_playwright_agent/profiles/sut_profiles.json`
2. Resolve SUT:
   - If `sut` is provided, use it.
   - Else match prompt text against `profiles` keys and `aliases` (case-insensitive).
3. Build parameter candidates from profile data:
   - prefer `profiles.<sut>.navigation.params` for navigation runs
   - otherwise map reusable fields such as `start_url`, `site_name`, `output_root`,
     `credentials_system_name`, `credentials_path`, `storage_state_path`, and limits
4. If `overrides_json` exists, parse and apply overrides.
5. Merge with precedence:
   - explicit prompt/tool arguments
   - `overrides_json`
   - profile defaults
   - tool safe defaults
6. Continue with the tool/workflow requested by the user:
   - use `run_manifest_first_route_workflow` for full navigation workflow requests
   - use other tools/workflows when the user requested those instead

## Parsing Rules

1. Support both English and Chinese terms.
2. Accept common typo `nevigation` as `navigation`.
3. If multiple SUT profiles match, ask one targeted clarification question.
4. If no SUT profile matches, ask one targeted clarification question listing available profiles.

## Clarification Rules

Ask at most one focused question when required values are still missing after profile merge.

Do not ask for parameters already present in profile JSON.

## Safety Rules

- Keep profile values serializable and auditable.
- Never store raw passwords in profile JSON.
- Use credential references/paths only.

## Defaults

- profile registry: `profiles/sut_profiles.json`

## Example Outcome Shape

Return a concise summary that includes:

- resolved SUT id
- profile source path
- merged parameter subset
- invoked tool/workflow name
- output paths (if execution occurred)
