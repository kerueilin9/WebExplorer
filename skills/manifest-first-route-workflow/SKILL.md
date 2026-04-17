---
name: manifest-first-route-workflow
description: Run a generic SUT manifest-first route workflow with guest/auth crawl, route task generation, and validation.
compatibility: google-adk>=1.31.0,<2.0
---

# Manifest-First Route Workflow

Use this skill when the user asks to explore a web application and generate
route navigation tasks. Keep the workflow generic across SUTs.

## Required Inputs

- `start_url`: the home page to start from.
- `site_name`: a short SUT name used for output paths and task ids.

## Optional Inputs

- `credentials_system_name`: the key used in the credentials file for login.
- `credentials_path`: credentials file path, if not using the default.
- `storage_state_path`: where authenticated browser state should be saved.
- `output_root`: where manifests and generated task folders should be written.
- `guest_max_depth`, `auth_max_depth`, `max_pages`, `auth_max_pages`.
- `include_patterns` / `exclude_patterns` for route filtering.
- `auth_include_patterns` / `auth_exclude_patterns` for auth-only filtering.
- `login_path`: explicit login route override. Omit this by default so the
  workflow can discover the login/sign-in route from the guest manifest.

## Execution Rules

1. Call `run_manifest_first_route_workflow` instead of manually chaining crawler,
   generator, and validator tools.
2. Use `sut_profile="generic"` unless the user explicitly provides a profile.
3. Keep `headed=true` and `persistent=true` unless the user asks otherwise.
4. Generate guest and authenticated manifests separately.
5. Let the workflow discover the login route from the guest manifest unless the
   user explicitly provides `login_path`.
6. Generate route navigation tasks only from stable manifests with no pending or
   error counts.
7. Report generated counts, skipped counts, validation issues, login path
   discovery source, and output paths.
8. Do not crawl or generate tasks for logout/signout/sign-off routes; they end
   the authenticated session and are not coverage targets.
9. Do not assume product-specific routes, labels, roles, or admin paths.

## Safe Defaults

- `output_root`: `manifests/<site_name>`
- guest manifest: `route_manifest.guest.generic.json`
- auth manifest: `route_manifest.auth.generic.json`
- guest task dir: `generated_tasks/guest`
- auth task dir: `generated_tasks/auth`
- `clean_task_dirs=true`, which removes only previous `task_*.json` files in
  the workflow-owned generated task directories.

## Example

```text
Run the manifest-first-route-workflow skill for http://localhost:3102.
Use site_name timeoff and credentials_system_name timeoff.
```
