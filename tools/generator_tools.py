"""Artifact generators for manifests and task JSON files."""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any

from adk_playwright_agent.app.policies import DANGEROUS_UI_KEYWORDS
from adk_playwright_agent.app.policies import is_session_ending_ui_label
from adk_playwright_agent.app.policies import resolve_workspace_path

_UNSAFE_ROUTE_KEYWORDS = DANGEROUS_UI_KEYWORDS | {
    "backup",
    "download",
    "export",
    "logout",
    "sign out",
    "upload",
}
_INVALID_QUERY_MARKERS = {"=nan", "=undefined"}


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
    payload = _task_payload(
        route=route,
        site_name=site_name,
        start_url=start_url,
        require_login=require_login,
        storage_state_path=storage_state_path,
        task_id=route.get("task_id"),
    )

    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "path": str(destination),
        "task_id": payload["task_id"],
        "label": route.get("label") or "Unnamed Route",
    }


def generate_tasks_from_manifest(
    manifest_path: str,
    output_dir: str,
    site_name: str,
    storage_state_path: str | None = None,
    require_login: bool | None = None,
    start_url: str | None = None,
    task_id_prefix: str | None = None,
    include_patterns: str | None = None,
    exclude_patterns: str | None = None,
    include_page_types: str | None = None,
    exclude_page_types: str | None = None,
    include_home: bool = True,
    include_unsafe_routes: bool = False,
    skip_invalid_query_routes: bool = True,
    max_tasks: int | None = None,
) -> dict[str, Any]:
    """Generate one navigation task JSON file per accepted route in a manifest."""

    manifest_file = resolve_workspace_path(manifest_path)
    destination_dir = resolve_workspace_path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    routes = manifest.get("routes", [])
    if not isinstance(routes, list):
        raise ValueError("manifest routes must be a JSON array.")

    inferred_start_url = start_url or manifest.get("start_url") or manifest.get("base_origin")
    if not inferred_start_url:
        raise ValueError("start_url is required when the manifest does not define one.")

    include = _split_patterns(include_patterns)
    exclude = _split_patterns(exclude_patterns)
    included_types = set(_split_patterns(include_page_types))
    excluded_types = set(_split_patterns(exclude_page_types))
    prefix = _slug(task_id_prefix or f"{site_name}_task")

    generated: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    seen_task_ids: set[str] = set()

    for route in routes:
        if not isinstance(route, dict):
            skipped.append({"reason": "route_not_object", "path": ""})
            continue

        skip_reason = _route_skip_reason(
            route=route,
            include_patterns=include,
            exclude_patterns=exclude,
            include_page_types=included_types,
            exclude_page_types=excluded_types,
            include_home=include_home,
            include_unsafe_routes=include_unsafe_routes,
            skip_invalid_query_routes=skip_invalid_query_routes,
        )
        if skip_reason:
            skipped.append(
                {
                    "reason": skip_reason,
                    "path": _route_key(route),
                    "label": str(route.get("label") or ""),
                }
            )
            continue

        if max_tasks is not None and len(generated) >= max_tasks:
            skipped.append(
                {
                    "reason": "max_tasks_reached",
                    "path": _route_key(route),
                    "label": str(route.get("label") or ""),
                }
            )
            continue

        route_requires_login = (
            bool(require_login)
            if require_login is not None
            else bool(route.get("require_login") or manifest.get("crawl_options", {}).get("crawl_authenticated"))
        )
        task_id = _unique_task_id(
            f"{prefix}_{len(generated) + 1:03d}_{_slug(_route_key(route))}",
            seen_task_ids,
        )
        payload = _task_payload(
            route=route,
            site_name=site_name,
            start_url=str(inferred_start_url),
            require_login=route_requires_login,
            storage_state_path=storage_state_path,
            task_id=task_id,
        )
        file_name = f"task_{task_id}.json"
        output_path = destination_dir / file_name
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        generated.append(
            {
                "path": str(output_path),
                "task_id": task_id,
                "label": str(route.get("label") or ""),
                "route": _route_key(route),
            }
        )

    return {
        "ok": True,
        "manifest_path": str(manifest_file),
        "output_dir": str(destination_dir),
        "site_name": site_name,
        "generated_count": len(generated),
        "skipped_count": len(skipped),
        "generated": generated,
        "skipped": skipped,
    }


def _task_payload(
    route: dict[str, Any],
    site_name: str,
    start_url: str,
    require_login: bool,
    storage_state_path: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    label = str(route.get("label") or "Unnamed Route")
    path = str(route.get("path") or "/")
    route_key = _route_key(route)
    navigation_steps = route.get("navigation_steps") or [
        "I open the configured home page",
    ]
    assertions = route.get("assertions") or [
        f'The browser URL should include "{path}"',
        f'The page title or primary heading should show "{label}"',
    ]
    resolved_task_id = task_id or route.get("task_id") or _task_id_from_route(site_name, route)

    return {
        "sites": [site_name],
        "task_id": resolved_task_id,
        "require_login": require_login,
        "storage_state": storage_state_path if require_login else None,
        "start_url": start_url,
        "geolocation": None,
        "gherkin": {
            "feature": f"{site_name} Navigation",
            "scenario": f"Navigate to {label}",
            "given": [
                "I am logged in to the site"
                if require_login
                else "I am visiting the site as a guest"
            ],
            "when": navigation_steps,
            "then": assertions,
        },
        "intent_template_id": 0,
        "require_reset": False,
        "eval": {
            "eval_types": ["gherkin_criteria"],
            "reference_answers": {
                "gherkin_acceptance_criteria": assertions,
            },
        },
        "route": {
            "path": path,
            "query": str(route.get("query") or ""),
            "route_key": route_key,
            "page_type": str(route.get("page_type") or ""),
        },
    }


def _route_skip_reason(
    route: dict[str, Any],
    include_patterns: list[str],
    exclude_patterns: list[str],
    include_page_types: set[str],
    exclude_page_types: set[str],
    include_home: bool,
    include_unsafe_routes: bool,
    skip_invalid_query_routes: bool,
) -> str | None:
    route_key = _route_key(route)
    page_type = str(route.get("page_type") or "")

    if not include_home and route_key == "/":
        return "home_skipped"
    if include_patterns and not _matches_any(route_key, include_patterns):
        return "include_pattern_mismatch"
    if exclude_patterns and _matches_any(route_key, exclude_patterns):
        return "exclude_pattern_match"
    if include_page_types and page_type not in include_page_types:
        return "include_page_type_mismatch"
    if exclude_page_types and page_type in exclude_page_types:
        return "exclude_page_type_match"
    if not include_unsafe_routes and _route_looks_unsafe(route):
        return "unsafe_route"
    if skip_invalid_query_routes and _route_has_invalid_query(route):
        return "invalid_query"
    return None


def _route_looks_unsafe(route: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(route.get(key) or "") for key in ("label", "path", "query", "url")
    ).lower()
    return is_session_ending_ui_label(haystack) or any(
        keyword in haystack for keyword in _UNSAFE_ROUTE_KEYWORDS
    )


def _route_has_invalid_query(route: dict[str, Any]) -> bool:
    query = str(route.get("query") or "").lower()
    return any(marker in f"?{query}" for marker in _INVALID_QUERY_MARKERS)


def _route_key(route: dict[str, Any]) -> str:
    path = str(route.get("path") or "/")
    query = str(route.get("query") or "")
    return f"{path}?{query}" if query else path


def _task_id_from_route(site_name: str, route: dict[str, Any]) -> str:
    return f"{_slug(site_name)}_task_{_slug(_route_key(route))}"


def _unique_task_id(task_id: str, seen: set[str]) -> str:
    candidate = task_id
    index = 2
    while candidate in seen:
        candidate = f"{task_id}_{index}"
        index += 1
    seen.add(candidate)
    return candidate


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _split_patterns(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _task_id_from_path(site_name: str, path_value: str) -> str:
    slug = _slug(path_value)
    return f"{_slug(site_name)}_task_{slug}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    slug = slug or "home"
    return slug
