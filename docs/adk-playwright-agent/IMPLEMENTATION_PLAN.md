# Implementation Plan

## Suggested Repository Layout

```text
adk_playwright_agent/
  app/
    agent.py
    prompts.py
    policies.py
    state_schema.py
    models.py
  tools/
    browser_tools.py
    workspace_tools.py
    generator_tools.py
    validation_tools.py
  adapters/
    playwright_cli.py
    credentials.py
  templates/
    task_json_template.json
    playwright_test_template.spec.ts
  eval/
    criteria/
      route_coverage.md
      output_quality.md
    fixtures/
      sample_routes.json
  README.md
```

## Main Agent Responsibilities

### Root Agent

Responsibilities:

- understand the user request
- decide output mode
- decide whether login is required
- sequence crawl, generation, and validation steps

Prompt responsibilities:

- prefer structured browser tools over free-form reasoning about unseen pages
- reuse stored auth state when available
- generate outputs in the current project's style if samples exist
- avoid destructive actions unless explicitly approved

### Browser Exploration Subflow

Responsibilities:

- start at the provided home page
- discover visible links
- normalize same-origin routes
- classify page types
- gather evidence needed for later assertions

### Output Generation Subflow

Responsibilities:

- map each accepted route into a navigation task (`Navigate to ...`)
- revisit canonical accepted routes with `playwright-cli` and infer page actions from live UI evidence
- map inferred actions into workflow tasks (`Create Employee`, etc.)
- preserve project conventions
- include login metadata when needed

### Validation Subflow

Responsibilities:

- check generated files parse correctly
- ensure each task starts from the requested entry page
- verify route coverage counts
- report skipped or ambiguous routes

## Recommended Phases

### Phase 1. Browser tool wrapper

Build a thin Python adapter over `playwright-cli`.

Deliverables:

- typed helper functions
- structured success results
- structured error objects

Exit criteria:

- can open, navigate, snapshot, click, fill, evaluate, and close

### Phase 2. ADK tool exposure

Wrap the Python helpers as ADK function tools.

Deliverables:

- browser toolset
- workspace toolset
- optional generator toolset

Exit criteria:

- the ADK agent can call tools deterministically in a local run

### Phase 3. Root agent orchestration

Implement the root agent instructions and state management.

Deliverables:

- crawl workflow
- login decision logic
- route normalization

Exit criteria:

- can crawl a simple site and produce a route manifest

### Phase 4. Navigation task generation

Use discovered routes to generate route-level `task_*.json`.

Deliverables:

- manifest file
- generated navigation tasks
- validation summary

Exit criteria:

- tasks are structurally valid
- each task contains navigation steps from the chosen home page

### Phase 5. Action intent discovery

Use browser-backed exploration to inspect each canonical accepted route for
workflow intents that can become test cases. This phase must not rely only on
static route manifest metadata.

Deliverables:

- canonical action route worklist with query-string variants folded into their base path
- per-route browser exploration evidence
- action intent catalog
- route-to-intent mapping
- safety classification for each intent

Exit criteria:

- create/edit/search/filter intents are detected from live page evidence when present
- each intent includes snapshot/DOM evidence sufficient to generate deterministic tasks
- routes such as `/calendar/teamview?department=1&date=2026-03` are folded into `/calendar/teamview` and not explored or generated separately

### Phase 6. Action task generation

Generate workflow tasks from the action intent catalog.

Deliverables:

- generated action tasks (`Create Employee`, `Edit Employee`, etc.)
- validation summary for action tasks

Exit criteria:

- generated action tasks are structurally valid
- tasks include concrete action steps and post-submit assertions

### Phase 7. Skill Packaging and Runtime Wiring

Package long repeatable workflows as ADK Skills and register them through
`skill_toolset.SkillToolset(...)`. Per the official ADK docs, Skills are
experimental in the stable 1.x line, so this phase should be implemented behind
version checks and smoke tests.

Deliverables:

- `manifest-first-route-workflow` skill package
- references and parameter presets for local SUT runs
- root agent wiring that loads file-based skills with `load_skill_from_dir(...)`
  and exposes the skill bundle through `from google.adk.tools import skill_toolset`

Exit criteria:

- operators can run the full manifest-first workflow via one skill-oriented prompt
- skill execution produces the same outputs as manual multi-step prompts
- skill guardrails are documented and enforced

### Phase 8. Evaluation

Add ADK evaluation to score route coverage and output quality.

Deliverables:

- route coverage criteria
- output quality criteria
- user simulation scenarios

Exit criteria:

- evaluation can flag missed routes, weak assertions, and malformed outputs

## MVP Acceptance Criteria

The MVP should be considered done when all of the following are true:

- the agent accepts a prompt such as "explore `http://localhost:3101` and generate tasks into `manifests/example_sut`"
- the agent opens `playwright-cli` with a headed persistent session
- the agent discovers guest routes from the home page
- the agent can optionally log in from a workspace credentials file
- the agent saves auth state for reuse
- the agent generates one navigation task per discovered target route
- every generated task starts from the configured home page
- task files are valid JSON
- a manifest summarizes coverage and generated outputs

## Expanded Acceptance Criteria (Action Tasks)

The expanded target should be considered done when all of the following are true:

- the agent uses `playwright-cli` to inspect each canonical accepted route for page-level workflows
- the agent generates action tasks for detected safe workflows
- create flows generate create-style tasks (for example, `Create Employee`)
- action tasks include form inputs, submit actions, and success assertions
- destructive or high-risk submits require explicit confirmation policy
- query-string variants are skipped or folded and do not produce separate action tasks

## Expanded Acceptance Criteria (Skillized Workflows)

The skillized target should be considered done when all of the following are true:

- the long manifest-first workflow is available as an ADK Skill
- users can trigger it with a concise prompt instead of listing every step
- the skill run reports generated_count, skipped_count, and validation issues for guest/auth outputs
- skill defaults remain overrideable through explicit prompt parameters

## Safety Policy

The first version should enforce these guardrails:

- default to read-only browsing until generation starts
- require confirmation for risky writes or destructive UI actions
- avoid direct submit or delete actions unless the user explicitly requests them
- keep all file writes inside a configured workspace root

## Evaluation Strategy

Use ADK evaluation for two categories:

### 1. Route Coverage

Questions:

- did the agent discover all same-origin routes visible from the selected navigation surfaces
- did it distinguish guest and signed-in routes correctly
- did it generate one output per accepted route

### 2. Output Quality

Questions:

- does each generated task start at the correct home page
- do navigation steps match an actual click path
- do assertions fit the page type
- do overlay workflows avoid over-reliance on URL checks
- do action tasks represent actual workflow intents observed through browser-backed page exploration

## Suggested Evaluation Fixtures

Start with small SUT-neutral fixtures, then validate against one or more local apps.

Fixture set should include:

- expected guest navigation routes
- expected signed-in routes
- expected admin routes
- one overlay workflow such as a create/edit dialog
- one page that should be skipped

## Known Risks

### UI text changes

Risk:

- link text and headings can vary with localization

Mitigation:

- allow fallback locators and DOM-derived labels

### Overlay and modal flows

Risk:

- some workflows do not have a distinct URL

Mitigation:

- validate by UI presence instead of URL alone

### Credential handling

Risk:

- agent may over-store sensitive data

Mitigation:

- keep only file paths and usernames in long-lived state when possible

### Tool misuse

Risk:

- agent may issue unnecessary or unsafe commands

Mitigation:

- expose only narrow tools
- apply confirmation on sensitive operations

## Why Not Start With MCP

MCP is a good long-term direction, but it should not be the first milestone.

Reasons:

- more moving parts
- more debugging overhead
- less direct visibility during early tool shaping

Recommended sequence:

1. Prove the browser tool contract with ADK function tools.
2. Stabilize prompts and state schema.
3. Extract the tool layer into MCP if cross-runtime reuse becomes important.

## Recommended First Prompt Template

Use a system instruction shaped like this:

```text
You are a web test authoring agent.

Your job is to explore a target web app using the provided browser tools,
identify navigable routes and important workflows, and generate structured
browser tasks in the current project's expected format.

Always begin from the configured home page.
Prefer same-origin navigation discovered from actual page content.
Do not use destructive actions unless explicitly allowed.
When login is required, use the configured credentials source and save
auth state for reuse.
For overlays or modal workflows, validate visible UI elements instead of
assuming a dedicated URL.
```

## Recommended Next Step After Design Approval

After approving this design, the next implementation slice should be:

1. keep manifest-first crawl as the first pass
2. generate route-level navigation tasks
3. add browser-backed route-to-action discovery as a second pass
4. generate action tasks from the intent catalog
5. validate both navigation and action task outputs
6. package the sequence as `manifest-first-route-workflow` skill
