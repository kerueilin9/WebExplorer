---
name: action-review-task-workflow
description: Run a generic SUT action review workflow from browser-backed action intents to reviewed action tasks.
compatibility: google-adk>=1.31.0,<2.0
---

# Action Review Task Workflow

Use this skill when the user asks to review browser-backed action intents and
generate page-action task files. Keep the workflow generic across SUTs.

## Required Inputs

- `intents_path`: a browser-backed `action_intents.browser*.json` file.
  If unavailable, create it first using `build_action_discovery_worklist` and
  `discover_page_actions_from_worklist`.

## Optional Inputs

- `site_name`: short SUT name used for task ids.
- `output_root`: where review packets, reviewed intents, and action task folders are written.
- `storage_state_path`: authenticated browser state used by generated tasks.
- `start_url`: expected task start URL. Omit it when the intents file references a worklist with start URL.
- `manifest_path`: route manifest used to bootstrap browser-backed action discovery.
- `worklist_path`: optional explicit path for action discovery worklist output.
- `action_intents_output_path`: optional explicit path for browser-backed action intents output.
- `reviewed_intents_json`: LLM review decisions for accepted/rejected/updated existing intent ids.
- `reviewed_intents_path`: existing reviewed intents file to use for task generation.
- `task_id_prefix`, `max_tasks`, and packet size limits.

## Execution Rules

1. If `intents_path` does not exist or is stale, run upstream discovery first:
  - call `build_action_discovery_worklist` from a route manifest
  - call `discover_page_actions_from_worklist` from that worklist
  - continue with the produced browser-backed `action_intents.browser*.json`
2. Call `run_action_review_task_workflow` instead of manually chaining packet
  generation, reviewed-intent writing, action-task generation, and validation.
3. If no `reviewed_intents_json` or `reviewed_intents_path` is available, keep
   `require_review=true`; the workflow should stop after review packet creation.
4. Review packets as a generic SUT reviewer. Do not assume product-specific
   routes, labels, roles, or admin paths.
5. During review, only accept, reject, rename, or reclassify existing
   `intent_id` values from the packet. Do not invent controls, routes, fields,
   or assertions.
6. Prefer visible headings, form labels, control labels, and observed browser
   evidence over URL tokens.
7. Reject low-value actions such as numeric-only labels, raw path labels, and
   generic personal links.
8. For create/edit workflows, provide executable reviewed fields when the
  evidence shows enough fields and a safe submit/save control. Include
  `workflow_steps`, `test_data`, `success_evidence`, and `commit_policy` in the
  reviewed intent decision. Create/edit intents missing reviewed workflow fields
  are skipped instead of generated as partial placeholder tasks.
9. Validate generated action task files before reporting completion.

## Upstream Discovery Sequence

Use this sequence when browser-backed intents are not ready yet:

1. Build canonical action discovery worklist from route manifest:
  - `build_action_discovery_worklist(manifest_path, output_path, site_name, ...)`
2. Discover browser-backed action evidence and intents from that worklist:
  - `discover_page_actions_from_worklist(worklist_path, output_path, evidence_dir, site_name, storage_state_path, ...)`
3. Use the produced `action_intents.browser*.json` as `intents_path` for
  `run_action_review_task_workflow`.

## Reviewed Intent Shape

When producing `reviewed_intents_json`, use this shape:

```json
{
  "reviewed_intents": [
    {
      "intent_id": "existing intent id from the packet",
      "decision": "accept",
      "label": "clear user-facing task label",
      "intent_type": "create",
      "workflow_steps": [
        "I open the \"Create item\" workflow.",
        "I fill \"Name\" with \"Action Review Test\".",
        "I submit the form."
      ],
      "test_data": {
        "Name": "Action Review Test"
      },
      "commit_policy": "Submit is allowed because this task is intended to create disposable test data.",
      "success_evidence": [
        "A confirmation or the created item should be visible."
      ],
      "review_notes": "Grounded in visible form fields and submit control."
    }
  ]
}
```

Only include executable submit/save steps when the task is safe, reversible or
test-data based, and grounded in observed fields/controls. Never include
destructive, session-ending, payment, import/export, or irreversible operations.

## Safe Defaults

- action worklist file: `action_worklist.generic.json`
- browser intents file: `action_intents.browser.generic.json`
- action discovery evidence dir: `action_discovery`
- review packet dir: `action_review_packets`
- reviewed intents file: `action_intents.reviewed.generic.json`
- action task dir: `generated_tasks/actions`
- `clear_existing=true`, which removes only previous `packet_*.json` files and
  previous `task_*.json` files in workflow-owned output directories.

## Example

```text
Build action discovery worklist from timeoff/route_manifest.auth.generic.json into timeoff/action_worklist.auth.generic.json.

Run browser-backed action discovery from timeoff/action_worklist.auth.generic.json into timeoff/action_intents.browser.auth.generic.json. Write evidence under timeoff/action_discovery.

Run the action-review-task-workflow skill for timeoff/action_intents.browser.auth.generic.json.
Use site_name timeoff, output_root timeoff, and storage_state_path .auth/timeoff_state.json.
First create review packets and stop for review.
```
