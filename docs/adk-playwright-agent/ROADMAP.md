# Roadmap

## Current State

Implemented today:

- headed persistent `playwright-cli` adapter
- guest/auth crawlers
- route manifest writing
- route-level navigation task generation
- validation helpers
- manifest-first workflow
- profile-backed short-prompt routing
- canonical action discovery worklist
- page observation artifacts using raw `playwright-cli snapshot` content
- Vertex-backed page summaries
- Vertex-backed page draft generation
- draft backlog merge/dedupe
- Vertex adapter retry/backoff for `429 RESOURCE_EXHAUSTED`

The current stable output boundary is:

- route navigation tasks
- page summaries
- page draft cases
- deduped draft backlog

## Near-Term Priorities

### 1. Improve Draft Quality

Main target:

- increase high-value `create` / `edit` / `delete` recall
- reduce low-value or duplicate drafts

Likely work:

- better prompt tuning
- better draft dedupe and ranking
- stronger happy-path retention for form pages
- better use of snapshot structure in Vertex prompts

### 2. Improve Observation Fidelity

Main target:

- keep page artifacts readable and useful to both humans and Vertex

Likely work:

- refine snapshot depth defaults per page type
- decide whether raw snapshot should stay embedded or move to sidecar files
- improve treatment of large pages and partial snapshots

### 3. Strengthen Vertex Stability

Main target:

- reduce failures under shared-capacity conditions

Likely work:

- keep `GOOGLE_CLOUD_LOCATION=global`
- monitor retry behavior
- optionally add request pacing between pages
- optionally add simple resume behavior for interrupted page-summary/draft runs

### 4. Tighten Profile and Workflow Experience

Main target:

- make short prompts reliable across navigation and draft workflows

Likely work:

- simplify profile schema where possible
- ensure workflow defaults match real tool signatures
- improve operator-facing examples

## Known Gaps

### Draft Backlog Is Still a Draft Product

The backlog is useful, but it is not yet a strong substitute for a human test
designer. Common remaining issues:

- too many weak or repetitive drafts on some pages
- not enough high-value drafts on others
- limited use of cross-page context when ranking

### Route Track and Draft Track Are Uneven

Route coverage is more mature and deterministic than page-draft generation.
That is expected today.

### ADK Main Model Path Can Still Hit Vertex Capacity

The custom Vertex adapter now retries `429 RESOURCE_EXHAUSTED`, but ADK's own
main LLM path still depends on Vertex capacity separately. When the root agent
is also using Vertex-backed Gemini, shared-capacity limits can still surface in
agent conversation turns.

## Deliberately Deferred

The following are not current priorities:

- final executable action-task generation for create/edit flows
- reintroducing static action-intent pipelines
- SUT-specific crawler behavior as the default
- multi-agent parallel execution for page drafting
- full MCP refactor

## Good Next Steps

If the goal is better draft quality, the next useful steps are:

1. run one or two representative SUTs end to end with the current snapshot-based page observations
2. inspect where good drafts are still being missed
3. refine prompts and reduction heuristics from those failures
4. add resume/rerun support so long Vertex runs recover cleanly

If the goal is operator usability, the next useful steps are:

1. simplify docs and examples
2. tighten profile defaults
3. keep workflow prompts short and explicit

## What Success Looks Like

This project is on the right track when:

- one short prompt can generate route tasks or a draft backlog for a generic SUT
- page observations are readable and grounded in real snapshot evidence
- the draft backlog is compact enough for a human to review quickly
- another web agent can use that backlog as input without needing to rediscover the SUT from scratch
