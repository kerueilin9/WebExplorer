"""Artifact generators for manifests and task JSON files."""

from __future__ import annotations

import json

from adk_playwright_agent.app.policies import resolve_workspace_path


def write_route_manifest(output_path: str, routes_json: str) -> dict:
    """Write a route manifest JSON file from a JSON string."""

    destination = resolve_workspace_path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    routes = json.loads(routes_json)
    if not isinstance(routes, list):
        raise ValueError("routes_json must decode to a JSON array.")
    manifest = {
        "route_count": len(routes),
        "routes": routes,
    }
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(destination),
        "route_count": len(routes),
    }


def generate_task_file(
    output_path: str,
    route_json: str,
    start_url: str,
    require_login: bool = False,
    storage_state_path: str | None = None,
    site_name: str = "webapp",
) -> dict:
    """Generate a task JSON file from normalized route metadata."""

    destination = resolve_workspace_path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    route = json.loads(route_json)
    label = route.get("label") or "Unnamed Route"
    path = route.get("path") or "/"
    navigation_steps = route.get("navigation_steps") or [
        "I open the configured home page",
    ]
    assertions = route.get("assertions") or [
        f'The browser URL should end with "{path}"',
        f'The page title or primary heading should show "{label}"',
    ]
    task_id = route.get("task_id") or _task_id_from_path(site_name, path)

    payload = {
        "sites": [site_name],
        "task_id": task_id,
        "require_login": require_login,
        "storage_state": storage_state_path if require_login else None,
        "start_url": start_url,
        "geolocation": None,
        "gherkin": {
            "feature": f"{site_name} Navigation",
            "scenario": f"Navigate to {label}",
            "given": [
                "I am logged in to the site" if require_login else "I am visiting the site as a guest"
            ],
            "when": navigation_steps,
            "then": assertions,
        },
        "require_reset": False,
        "eval": {
            "eval_types": ["gherkin_criteria"],
            "reference_answers": {
                "gherkin_acceptance_criteria": assertions,
            },
        },
    }

    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(destination),
        "task_id": task_id,
        "label": label,
    }


def _task_id_from_path(site_name: str, path_value: str) -> str:
    slug = path_value.strip("/").replace("/", "_").replace("-", "_")
    slug = slug or "home"
    return f"{site_name}_task_{slug}"
