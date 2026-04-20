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


def generate_action_tasks_from_intents(
    intents_path: str,
    output_dir: str,
    site_name: str | None = None,
    start_url: str | None = None,
    storage_state_path: str | None = None,
    require_login: bool | None = None,
    task_id_prefix: str | None = None,
    include_intent_types: str | None = None,
    exclude_intent_types: str | None = None,
    include_safety_levels: str | None = "read_only,safe_with_confirmation",
    clear_existing: bool = False,
    max_tasks: int | None = None,
) -> dict[str, Any]:
    """Generate action workflow task JSON files from browser-backed action intents."""

    intents_file = resolve_workspace_path(intents_path)
    destination_dir = resolve_workspace_path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    if clear_existing:
        _remove_existing_task_files(destination_dir)

    payload = json.loads(intents_file.read_text(encoding="utf-8"))
    intents = payload.get("intents", [])
    if not isinstance(intents, list):
        raise ValueError("intents must be a JSON array.")

    worklist = _load_action_worklist(payload)
    inferred_site_name = site_name or str(payload.get("site_name") or worklist.get("site_name") or "webapp")
    inferred_start_url = start_url or str(worklist.get("start_url") or worklist.get("base_origin") or "")
    if not inferred_start_url:
        raise ValueError("start_url is required when the action intent file does not reference a worklist with start_url.")

    include_types = set(_split_patterns(include_intent_types))
    exclude_types = set(_split_patterns(exclude_intent_types))
    include_safety = set(_split_patterns(include_safety_levels))
    route_index = _worklist_route_index(worklist)
    target_route_intents = _target_route_intent_keys(intents)
    prefix = _slug(task_id_prefix or f"{inferred_site_name}_action")

    generated: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    seen_task_ids: set[str] = set()

    for intent in intents:
        if not isinstance(intent, dict):
            skipped.append({"reason": "intent_not_object", "intent_id": ""})
            continue

        skip_reason = _action_intent_skip_reason(
            intent=intent,
            include_types=include_types,
            exclude_types=exclude_types,
            include_safety=include_safety,
            target_route_intents=target_route_intents,
        )
        if skip_reason:
            skipped.append(_skipped_action_intent(intent, skip_reason))
            continue

        if max_tasks is not None and len(generated) >= max_tasks:
            skipped.append(_skipped_action_intent(intent, "max_tasks_reached"))
            continue

        route = _route_for_action_intent(intent, route_index)
        intent_requires_login = (
            bool(require_login)
            if require_login is not None
            else bool(intent.get("require_login") or route.get("require_login"))
        )
        task_id = _unique_task_id(
            f"{prefix}_{len(generated) + 1:03d}_{_slug(str(intent.get('intent_id') or intent.get('label') or 'action'))}",
            seen_task_ids,
        )
        task_payload = _action_task_payload(
            intent=intent,
            route=route,
            site_name=inferred_site_name,
            start_url=str(inferred_start_url),
            require_login=intent_requires_login,
            storage_state_path=storage_state_path,
            task_id=task_id,
        )
        output_path = destination_dir / f"task_{task_id}.json"
        output_path.write_text(
            json.dumps(task_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        generated.append(
            {
                "path": str(output_path),
                "task_id": task_id,
                "label": str(intent.get("label") or ""),
                "intent_type": str(intent.get("intent_type") or ""),
                "route": str(intent.get("entry_path") or route.get("canonical_path") or "/"),
            }
        )

    return {
        "ok": True,
        "intents_path": str(intents_file),
        "output_dir": str(destination_dir),
        "site_name": inferred_site_name,
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


def _action_task_payload(
    intent: dict[str, Any],
    route: dict[str, Any],
    site_name: str,
    start_url: str,
    require_login: bool,
    storage_state_path: str | None,
    task_id: str,
) -> dict[str, Any]:
    intent_type = str(intent.get("intent_type") or "action")
    label = str(intent.get("label") or intent_type.title())
    entry_path = str(intent.get("entry_path") or route.get("canonical_path") or "/")
    navigation_steps = list(route.get("navigation_steps") or ["I open the configured home page"])
    action_steps = _action_steps(intent)
    assertions = _action_assertions(intent, route)

    return {
        "sites": [site_name],
        "task_id": task_id,
        "require_login": require_login,
        "storage_state": storage_state_path if require_login else None,
        "start_url": start_url,
        "geolocation": None,
        "gherkin": {
            "feature": f"{site_name} Actions",
            "scenario": _action_scenario(intent),
            "given": [
                "I am logged in to the site"
                if require_login
                else "I am visiting the site as a guest"
            ],
            "when": navigation_steps + action_steps,
            "then": assertions,
        },
        "intent_template_id": 0,
        "require_reset": intent_type in {"create", "edit"},
        "eval": {
            "eval_types": ["gherkin_criteria"],
            "reference_answers": {
                "gherkin_acceptance_criteria": assertions,
            },
        },
        "route": {
            "path": entry_path.split("?", 1)[0] or "/",
            "query": entry_path.split("?", 1)[1] if "?" in entry_path else "",
            "route_key": entry_path,
            "page_type": str(route.get("page_type") or ""),
        },
        "action_intent": {
            "intent_id": str(intent.get("intent_id") or ""),
            "intent_type": intent_type,
            "label": label,
            "entity": str(intent.get("entity") or ""),
            "safety_level": str(intent.get("safety_level") or ""),
            "confidence": intent.get("confidence"),
            "source": intent.get("source", {}),
        },
    }


def _action_steps(intent: dict[str, Any]) -> list[str]:
    intent_type = str(intent.get("intent_type") or "")
    label = str(intent.get("label") or "the action").strip() or "the action"
    reviewed_steps = _reviewed_workflow_steps(intent)
    if reviewed_steps:
        return reviewed_steps

    fields = [str(field) for field in intent.get("input_fields", []) if str(field).strip()]
    source = intent.get("source", {}) if isinstance(intent.get("source"), dict) else {}
    source_kind = str(source.get("kind") or "")

    if intent_type == "search":
        field_label = fields[0] if fields else label
        return [f'I search using the "{field_label}" field.']
    if intent_type == "filter":
        if source_kind == "browser_form":
            field_label = fields[0] if fields else label
            return [f'I apply or inspect the "{field_label}" filter.']
        return [f'I use the "{label}" filter control.']
    if intent_type in {"create", "edit"}:
        return [
            f'I open the "{label}" {intent_type} workflow.',
            "I stop before submitting, saving, or otherwise committing changes.",
        ]
    if intent_type == "open":
        return [f'I open the "{label}" UI.']
    return [f'I use the "{label}" action.']


def _action_assertions(intent: dict[str, Any], route: dict[str, Any]) -> list[str]:
    intent_type = str(intent.get("intent_type") or "")
    label = str(intent.get("label") or "the action").strip() or "the action"
    entry_path = str(intent.get("entry_path") or route.get("canonical_path") or "/")
    assertions = [f'The browser URL should include "{entry_path.split("?", 1)[0] or "/"}"']
    reviewed_assertions = _reviewed_success_evidence(intent)
    if reviewed_assertions:
        assertions.extend(reviewed_assertions)
        return assertions

    if intent_type in {"search", "filter"}:
        success = [str(value) for value in intent.get("success_evidence", []) if str(value).strip()]
        assertions.extend(success or ["The page should show matching results or a stable filtered state."])
    elif intent_type in {"create", "edit"}:
        assertions.extend(
            [
                f'The "{label}" workflow entrypoint or form should be visible.',
                "No submit, save, delete, approve, reject, import, upload, or signout action should be completed.",
            ]
        )
    elif intent_type == "open":
        assertions.append(f'The "{label}" UI should become visible.')
    else:
        assertions.append("The intended UI outcome should be visible.")

    return assertions


def _action_scenario(intent: dict[str, Any]) -> str:
    intent_type = str(intent.get("intent_type") or "action").replace("_", " ").title()
    label = str(intent.get("label") or "Action").strip() or "Action"
    if _reviewed_workflow_steps(intent):
        return f"{intent_type} {label}"
    if str(intent.get("intent_type") or "") in {"create", "edit"}:
        return f"Open {label} {intent_type.lower()} workflow"
    return f"{intent_type} with {label}"


def _reviewed_workflow_steps(intent: dict[str, Any]) -> list[str]:
    review = intent.get("review", {}) if isinstance(intent.get("review"), dict) else {}
    raw_steps = review.get("workflow_steps") or intent.get("workflow_steps")
    if not isinstance(raw_steps, list):
        return []
    steps = [str(step).strip() for step in raw_steps if str(step).strip()]
    if not steps:
        return []
    policy = str(review.get("commit_policy") or intent.get("commit_policy") or "").strip()
    if policy:
        steps.append(f"Commit policy: {policy.rstrip('.')}.")
    return steps


def _reviewed_success_evidence(intent: dict[str, Any]) -> list[str]:
    review = intent.get("review", {}) if isinstance(intent.get("review"), dict) else {}
    raw_assertions = review.get("success_evidence") or intent.get("reviewed_success_evidence")
    if not isinstance(raw_assertions, list):
        return []
    return [str(assertion).strip() for assertion in raw_assertions if str(assertion).strip()]


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


def _action_intent_skip_reason(
    intent: dict[str, Any],
    include_types: set[str],
    exclude_types: set[str],
    include_safety: set[str],
    target_route_intents: set[tuple[str, str]],
) -> str | None:
    intent_type = str(intent.get("intent_type") or "")
    safety = str(intent.get("safety_level") or "")
    if include_types and intent_type not in include_types:
        return "include_intent_type_mismatch"
    if exclude_types and intent_type in exclude_types:
        return "exclude_intent_type_match"
    if include_safety and safety not in include_safety:
        return "safety_level_excluded"
    if safety == "high_risk":
        return "high_risk"
    if _action_intent_has_low_value_label(intent):
        return "low_value_action_label"
    source = intent.get("source", {}) if isinstance(intent.get("source"), dict) else {}
    if source.get("kind") == "browser_control" and intent_type in {"create", "edit"}:
        control_path = _normalize_task_route_key(str(source.get("control_path") or ""))
        if control_path and (control_path, intent_type) in target_route_intents:
            return "covered_by_target_route_intent"
    return None


def _action_intent_has_low_value_label(intent: dict[str, Any]) -> bool:
    label = str(intent.get("label") or "").strip()
    normalized = label.lower()
    if not label:
        return True
    if normalized in {"me", "more", "more...", "toggle dropdown"}:
        return True
    if re.fullmatch(r"\d+", normalized):
        return True
    if normalized.startswith("/") and " " not in normalized:
        return True
    if re.fullmatch(r"[\W_]+", normalized):
        return True
    return False


def _skipped_action_intent(intent: dict[str, Any], reason: str) -> dict[str, str]:
    return {
        "reason": reason,
        "intent_id": str(intent.get("intent_id") or ""),
        "intent_type": str(intent.get("intent_type") or ""),
        "label": str(intent.get("label") or ""),
        "route": str(intent.get("entry_path") or ""),
    }


def _load_action_worklist(payload: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(payload.get("worklist_path") or "")
    if not raw_path:
        return {}
    try:
        worklist_file = resolve_workspace_path(raw_path)
    except ValueError:
        return {}
    if not worklist_file.exists():
        return {}
    decoded = json.loads(worklist_file.read_text(encoding="utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def _target_route_intent_keys(intents: list[Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for intent in intents:
        if not isinstance(intent, dict):
            continue
        source = intent.get("source", {}) if isinstance(intent.get("source"), dict) else {}
        intent_type = str(intent.get("intent_type") or "")
        if source.get("kind") != "browser_route" or intent_type not in {"create", "edit"}:
            continue
        entry_path = _normalize_task_route_key(str(intent.get("entry_path") or ""))
        if entry_path:
            keys.add((entry_path, intent_type))
    return keys


def _worklist_route_index(worklist: dict[str, Any]) -> dict[str, dict[str, Any]]:
    routes = worklist.get("routes", [])
    if not isinstance(routes, list):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for route in routes:
        if not isinstance(route, dict):
            continue
        for key in (
            str(route.get("route_id") or ""),
            str(route.get("worklist_id") or ""),
            str(route.get("canonical_path") or ""),
        ):
            if key:
                index[key] = route
    return index


def _route_for_action_intent(
    intent: dict[str, Any],
    route_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    route = route_index.get(str(intent.get("route_id") or ""))
    if route:
        return route
    route = route_index.get(str(intent.get("entry_path") or ""))
    if route:
        return route
    entry_path = str(intent.get("entry_path") or "/")
    return {
        "canonical_path": entry_path,
        "label": str(intent.get("label") or entry_path),
        "page_type": "",
        "phase": str(intent.get("phase") or ""),
        "require_login": bool(intent.get("require_login")),
        "navigation_steps": ["I open the configured home page"],
    }


def _remove_existing_task_files(destination_dir: Any) -> None:
    for path in destination_dir.glob("task_*.json"):
        if path.is_file():
            path.unlink()


def _normalize_task_route_key(value: str) -> str:
    if not value:
        return ""
    path = value.split("?", 1)[0].strip()
    if path != "/":
        path = path.rstrip("/")
    return path or "/"


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
