"""LLM-first action task discovery helpers.

These tools intentionally stop before final task generation. The browser
observations give the LLM full-page evidence, and the backlog tool only
normalizes the LLM-authored drafts into a safe execution order.
"""

from __future__ import annotations

import json
import re
import fnmatch
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from adk_playwright_agent.adapters.playwright_cli import PlaywrightCliAdapter
from adk_playwright_agent.app.policies import (
    DANGEROUS_UI_KEYWORDS,
    is_session_ending_ui_label,
    resolve_workspace_path,
)

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard
    yaml = None

_ADAPTER = PlaywrightCliAdapter()

_ACTION_PAGE_DATA_SCRIPT = (
    "JSON.stringify((() => {"
    "const text = el => (el.innerText || el.textContent || el.value || '').replace(/\\s+/g, ' ').trim();"
    "const attr = (el, name) => el.getAttribute(name) || '';"
    "const visible = el => { const s = getComputedStyle(el); const r = el.getBoundingClientRect(); return s && s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0; };"
    "const esc = value => window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/\"/g, '\\\\\"');"
    "const labelFor = el => {"
    "  if (el.labels && el.labels[0]) return text(el.labels[0]);"
    "  const id = attr(el, 'id');"
    "  if (id) { const label = document.querySelector('label[for=\"' + esc(id) + '\"]'); if (label) return text(label); }"
    "  return attr(el, 'aria-label') || attr(el, 'placeholder') || attr(el, 'name') || attr(el, 'type') || el.tagName.toLowerCase();"
    "};"
    "const safeUrl = href => { try { return new URL(href, location.href); } catch { return null; } };"
    "const selectorFor = el => {"
    "  if (!el) return '';"
    "  if (el.id) return '#' + esc(el.id);"
    "  if (attr(el, 'name')) return el.tagName.toLowerCase() + '[name=\"' + attr(el, 'name').replace(/\"/g, '\\\\\"') + '\"]';"
    "  if (attr(el, 'href')) return el.tagName.toLowerCase() + '[href=\"' + attr(el, 'href').replace(/\"/g, '\\\\\"') + '\"]';"
    "  return el.tagName.toLowerCase();"
    "};"
    "const forms = [...document.querySelectorAll('input,textarea,select')].filter(visible).map(el => ({"
    "  tag: el.tagName.toLowerCase(), type: attr(el, 'type'), name: attr(el, 'name'), label: labelFor(el),"
    "  placeholder: attr(el, 'placeholder'), aria_label: attr(el, 'aria-label'), required: !!el.required, disabled: !!el.disabled, selector: selectorFor(el)"
    "})).slice(0, 100);"
    "const controls = [...document.querySelectorAll('button,[role=\"button\"],a[href],input[type=\"submit\"],summary')].filter(visible).map(el => {"
    "  const url = attr(el, 'href') ? safeUrl(attr(el, 'href')) : null;"
    "  return { label: text(el), tag: el.tagName.toLowerCase(), role: attr(el, 'role'), type: attr(el, 'type'),"
    "    href: url ? url.href : '', path: url ? url.pathname : '', query: url ? url.search.slice(1) : '',"
    "    same_origin: url ? url.origin === location.origin : true, disabled: !!el.disabled || attr(el, 'aria-disabled') === 'true', selector: selectorFor(el) };"
    "}).filter(x => x.label || x.href).slice(0, 160);"
    "const headings = [...document.querySelectorAll('h1,h2,h3,[role=\"heading\"]')].filter(visible).map(text).filter(Boolean).slice(0, 30);"
    "const tables = [...document.querySelectorAll('table,[role=\"table\"],.table')].filter(visible).map(el => ({ text: text(el).slice(0, 240) })).slice(0, 12);"
    "return { url: location.href, title: document.title, headings, forms, controls, tables };"
    "})())"
)


def build_action_discovery_worklist(
    manifest_path: str,
    output_path: str,
    site_name: str | None = None,
    include_patterns: str | None = None,
    exclude_patterns: str | None = None,
    skip_query_variants: bool = True,
    include_unsafe_routes: bool = False,
    max_routes: int | None = None,
) -> dict[str, Any]:
    """Build a canonical route worklist for LLM-first page observation."""

    manifest_file = resolve_workspace_path(manifest_path)
    destination = resolve_workspace_path(output_path)
    if not manifest_file.exists() or not manifest_file.is_file():
        return _error_result(
            "file_not_found",
            f"File not found: {manifest_file}",
            manifest_path=str(manifest_file),
            output_path=str(destination),
            site_name=_slug(site_name or "webapp"),
            canonical_route_count=0,
            folded_variant_count=0,
            skipped_count=0,
        )

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error_result(
            "invalid_json",
            f"Invalid JSON in '{manifest_file}': {exc}",
            manifest_path=str(manifest_file),
            output_path=str(destination),
            site_name=_slug(site_name or "webapp"),
            canonical_route_count=0,
            folded_variant_count=0,
            skipped_count=0,
        )

    routes = manifest.get("routes", [])
    if not isinstance(routes, list):
        return _error_result(
            "invalid_manifest_schema",
            "manifest routes must be a JSON array.",
            manifest_path=str(manifest_file),
            output_path=str(destination),
            site_name=_slug(site_name or "webapp"),
            canonical_route_count=0,
            folded_variant_count=0,
            skipped_count=0,
        )

    include = _split_patterns(include_patterns)
    exclude = _split_patterns(exclude_patterns)
    inferred_site_name = _slug(site_name or _site_name_from_manifest(manifest))
    base_origin = str(manifest.get("base_origin") or manifest.get("start_url") or "")

    canonical: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    folded_variant_count = 0

    for route in routes:
        if not isinstance(route, dict):
            skipped.append({"reason": "route_not_object"})
            continue
        path = _normalize_route_key(str(route.get("path") or route.get("url") or "/"))
        query = str(route.get("query") or "")
        route_key = f"{path}?{query}" if query else path
        canonical_key = path if skip_query_variants else route_key

        if include and not _matches_any(canonical_key, include):
            skipped.append(_skipped_route(route, "include_pattern_mismatch", canonical_key))
            continue
        if exclude and _matches_any(canonical_key, exclude):
            skipped.append(_skipped_route(route, "exclude_pattern_match", canonical_key))
            continue
        if not include_unsafe_routes and _route_is_unsafe(route):
            skipped.append(_skipped_route(route, "unsafe_route", canonical_key))
            continue
        if canonical_key in canonical:
            canonical[canonical_key]["folded_variants"].append(route_key)
            folded_variant_count += 1
            continue
        if max_routes is not None and len(canonical) >= max_routes:
            skipped.append(_skipped_route(route, "max_routes_reached", canonical_key))
            continue
        canonical[canonical_key] = _worklist_route_payload(
            route,
            site_name=inferred_site_name,
            base_origin=base_origin,
            canonical_key=canonical_key,
            route_key=route_key,
        )

    payload = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "source_manifest_path": str(manifest_file),
        "site_name": inferred_site_name,
        "start_url": str(manifest.get("start_url") or base_origin),
        "base_origin": base_origin,
        "options": {
            "skip_query_variants": skip_query_variants,
            "include_unsafe_routes": include_unsafe_routes,
            "include_patterns": include,
            "exclude_patterns": exclude,
            "max_routes": max_routes,
        },
        "summary": {
            "canonical_route_count": len(canonical),
            "folded_variant_count": folded_variant_count,
            "skipped_count": len(skipped),
        },
        "routes": list(canonical.values()),
        "skipped_routes": skipped,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "manifest_path": str(manifest_file),
        "output_path": str(destination),
        "site_name": inferred_site_name,
        "canonical_route_count": len(canonical),
        "folded_variant_count": folded_variant_count,
        "skipped_count": len(skipped),
    }


def observe_task_pages_from_worklist(
    worklist_path: str,
    output_path: str,
    observation_dir: str | None = None,
    site_name: str | None = None,
    storage_state_path: str | None = None,
    session_name: str = "task-observation",
    headed: bool = True,
    persistent: bool = True,
    max_routes: int | None = None,
    max_forms_per_route: int = 80,
    close_on_finish: bool = True,
) -> dict[str, Any]:
    """Open canonical routes and write page observations for LLM-first task ideation."""

    worklist_file = resolve_workspace_path(worklist_path)
    destination = resolve_workspace_path(output_path)

    if not worklist_file.exists() or not worklist_file.is_file():
        return _error_result(
            "file_not_found",
            f"File not found: {worklist_file}",
            worklist_path=str(worklist_file),
            output_path=str(destination),
        )

    try:
        worklist = json.loads(worklist_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error_result(
            "invalid_json",
            f"Invalid JSON in '{worklist_file}': {exc}",
            worklist_path=str(worklist_file),
            output_path=str(destination),
        )

    routes = worklist.get("routes", [])
    if not isinstance(routes, list):
        return _error_result(
            "invalid_worklist_schema",
            "worklist routes must be a JSON array.",
            worklist_path=str(worklist_file),
            output_path=str(destination),
        )

    observation_root = resolve_workspace_path(
        observation_dir or str(destination.parent / "page_observations")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    observation_root.mkdir(parents=True, exist_ok=True)

    inferred_site_name = _slug(site_name or worklist.get("site_name") or "webapp")
    start_url = str(worklist.get("start_url") or worklist.get("base_origin") or "")
    if not start_url and routes:
        start_url = str(routes[0].get("selected_url") or "")

    observation_files: list[str] = []
    observations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    open_result = _ADAPTER.open_browser(
        base_url=start_url,
        session_name=session_name,
        headed=headed,
        persistent=persistent,
    )
    if not open_result.ok:
        errors.append(
            {
                "reason": "open_browser_failed",
                "message": open_result.stderr or open_result.stdout,
            }
        )
        _write_observation_index(
            destination,
            worklist_file=worklist_file,
            site_name=inferred_site_name,
            observation_dir=observation_root,
            observations=[],
            observation_files=[],
            skipped=skipped,
            errors=errors,
        )
        return _error_result(
            "open_browser_failed",
            open_result.stderr or open_result.stdout,
            output_path=str(destination),
            error_count=len(errors),
        )

    if storage_state_path:
        state_path = resolve_workspace_path(storage_state_path)
        state_result = _ADAPTER.load_storage_state(session_name=session_name, path=str(state_path))
        if not state_result.ok:
            errors.append(
                {
                    "reason": "state_load_failed",
                    "path": str(state_path),
                    "message": state_result.stderr or state_result.stdout,
                }
            )

    processed_routes = 0
    try:
        if not errors:
            for route in routes:
                if not isinstance(route, dict):
                    skipped.append({"reason": "route_not_object"})
                    continue
                if max_routes is not None and processed_routes >= max_routes:
                    skipped.append(
                        {
                            "reason": "max_routes_reached",
                            "route": str(route.get("canonical_path") or ""),
                            "route_id": str(route.get("route_id") or ""),
                        }
                    )
                    continue

                processed_routes += 1
                observation_file, summary, route_errors = _observe_one_task_route(
                    route=route,
                    page_number=processed_routes,
                    session_name=session_name,
                    observation_root=observation_root,
                    max_forms_per_route=max_forms_per_route,
                )
                observation_files.append(observation_file)
                observations.append(summary)
                errors.extend(route_errors)
    finally:
        if close_on_finish:
            _ADAPTER.close_browser(session_name=session_name)

    _write_observation_index(
        destination,
        worklist_file=worklist_file,
        site_name=inferred_site_name,
        observation_dir=observation_root,
        observations=observations,
        observation_files=observation_files,
        skipped=skipped,
        errors=errors,
    )
    return {
        "ok": not errors,
        "worklist_path": str(worklist_file),
        "output_path": str(destination),
        "observation_dir": str(observation_root),
        "site_name": inferred_site_name,
        "route_count": processed_routes,
        "observation_count": len(observations),
        "skipped_count": len(skipped),
        "error_count": len(errors),
    }


def consolidate_task_drafts_to_backlog(
    drafts_path: str,
    output_path: str,
    site_name: str | None = None,
    max_tasks: int | None = None,
    include_categories: str | None = None,
    exclude_categories: str | None = None,
) -> dict[str, Any]:
    """Deduplicate LLM-authored task drafts into a prioritized execution backlog."""

    drafts_file = resolve_workspace_path(drafts_path)
    destination = resolve_workspace_path(output_path)

    if not drafts_file.exists() or not drafts_file.is_file():
        return _error_result(
            "file_not_found",
            f"File not found: {drafts_file}",
            drafts_path=str(drafts_file),
            output_path=str(destination),
        )

    try:
        payload = json.loads(drafts_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error_result(
            "invalid_json",
            f"Invalid JSON in '{drafts_file}': {exc}",
            drafts_path=str(drafts_file),
            output_path=str(destination),
        )

    try:
        drafts = _decode_task_drafts(payload)
    except ValueError as exc:
        return _error_result(
            "invalid_drafts_schema",
            str(exc),
            drafts_path=str(drafts_file),
            output_path=str(destination),
        )

    include = set(_split_patterns(include_categories))
    exclude = set(_split_patterns(exclude_categories))
    inferred_site_name = _slug(site_name or _task_draft_site_name(payload) or "webapp")

    tasks_by_key: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for index, draft in enumerate(drafts, start=1):
        if not isinstance(draft, dict):
            skipped.append({"reason": "draft_not_object", "index": index})
            continue
        normalized = _normalize_task_draft(draft, site_name=inferred_site_name, index=index)
        category = str(normalized["category"])
        if include and category not in include:
            skipped.append(_skipped_task_draft(normalized, "include_category_mismatch"))
            continue
        if exclude and category in exclude:
            skipped.append(_skipped_task_draft(normalized, "exclude_category_match"))
            continue
        key = str(normalized["dedupe_key"])
        if key in tasks_by_key:
            _merge_duplicate_task_draft(tasks_by_key[key], normalized)
            skipped.append(
                _skipped_task_draft(
                    normalized,
                    "duplicate",
                    canonical_id=str(tasks_by_key[key]["backlog_id"]),
                )
            )
            continue
        if max_tasks is not None and len(tasks_by_key) >= max_tasks:
            skipped.append(_skipped_task_draft(normalized, "max_tasks_reached"))
            continue
        tasks_by_key[key] = normalized

    tasks = sorted(
        tasks_by_key.values(),
        key=lambda task: (
            int(task["execution_order"]),
            str(task["priority"]),
            str(task["category"]),
            str(task["backlog_id"]),
        ),
    )
    backlog = _task_backlog_payload(
        drafts_file=drafts_file,
        site_name=inferred_site_name,
        drafts=drafts,
        tasks=tasks,
        skipped=skipped,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(backlog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "drafts_path": str(drafts_file),
        "output_path": str(destination),
        "site_name": inferred_site_name,
        "draft_count": len(drafts),
        "backlog_count": len(tasks),
        "skipped_count": len(skipped),
        "by_category": backlog["summary"]["by_category"],
        "by_priority": backlog["summary"]["by_priority"],
        "by_execution_policy": backlog["summary"]["by_execution_policy"],
    }


def _observe_one_task_route(
    *,
    route: dict[str, Any],
    page_number: int,
    session_name: str,
    observation_root: Path,
    max_forms_per_route: int,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    canonical_path = str(route.get("canonical_path") or "/")
    route_id = str(route.get("route_id") or _slug(canonical_path))
    selected_url = str(route.get("selected_url") or canonical_path)
    page_id = f"page-{page_number:03d}"
    observation_file = observation_root / f"{page_id}-{_slug(route_id or canonical_path)}.yml"
    errors: list[dict[str, Any]] = []

    goto_result = _ADAPTER.goto(session_name=session_name, url=selected_url)
    if not goto_result.ok:
        errors.append(
            {
                "route_id": route_id,
                "route": canonical_path,
                "reason": "goto_failed",
                "message": goto_result.stderr or goto_result.stdout,
            }
        )
        observation = _task_page_observation_payload(
            page_id=page_id,
            route=route,
            baseline={},
            page_data={},
            snapshot_content="",
            forms=[],
            tables=[],
            errors=errors,
        )
        _write_page_artifact(observation_file, observation)
        return str(observation_file), _task_observation_summary(observation_file, route, observation), errors

    snapshot_result = _ADAPTER.snapshot(session_name=session_name, depth=10)
    snapshot_path, snapshot_content, snapshot_error = _load_snapshot_artifact(
        snapshot_result.snapshot_path,
        inline_snapshot_content=snapshot_result.snapshot_content,
    )
    page_data, page_error = _collect_page_data(session_name)
    if page_error:
        errors.append(
            {
                "route_id": route_id,
                "route": canonical_path,
                "reason": "page_data_failed",
                "message": page_error,
            }
        )
    if snapshot_error:
        errors.append(
            {
                "route_id": route_id,
                "route": canonical_path,
                "reason": "snapshot_read_failed",
                "message": snapshot_error,
            }
        )

    forms = _clean_live_forms(page_data.get("forms", []))[: max(max_forms_per_route, 0)]
    if route.get("require_login") and _page_looks_like_auth_redirect(page_data):
        errors.append(
            {
                "route_id": route_id,
                "route": canonical_path,
                "reason": "auth_redirect_detected",
                "url": str(page_data.get("url") or goto_result.url or ""),
            }
        )

    baseline = {
        "url": str(page_data.get("url") or goto_result.url or selected_url),
        "title": str(page_data.get("title") or goto_result.title or ""),
        "headings": _clean_strings(page_data.get("headings", [])),
        "snapshot_path": str(snapshot_path or snapshot_result.snapshot_path or ""),
        "snapshot_ok": snapshot_result.ok and bool(snapshot_content),
    }
    observation = _task_page_observation_payload(
        page_id=page_id,
        route=route,
        baseline=baseline,
        page_data=page_data,
        snapshot_content=snapshot_content,
        forms=forms,
        tables=page_data.get("tables", []) if isinstance(page_data.get("tables"), list) else [],
        errors=errors,
    )
    _write_page_artifact(observation_file, observation)
    return str(observation_file), _task_observation_summary(observation_file, route, observation), errors


def _collect_page_data(session_name: str) -> tuple[dict[str, Any], str | None]:
    result = _ADAPTER.eval_js(session_name=session_name, script=_ACTION_PAGE_DATA_SCRIPT, raw=True)
    if not result.ok:
        return {}, result.stderr or result.stdout or "eval_js failed"
    value = result.raw_value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}, "eval_js did not return JSON"
    if not isinstance(value, dict):
        return {}, "eval_js returned a non-object payload"
    return value, None


def _load_snapshot_artifact(
    snapshot_path: str | None,
    *,
    inline_snapshot_content: str | None = None,
) -> tuple[Path | None, str, str | None]:
    if inline_snapshot_content:
        return None, inline_snapshot_content, None
    if not snapshot_path:
        return None, "", "playwright-cli did not return snapshot content or a snapshot path"
    candidate = Path(snapshot_path)
    if not candidate.is_absolute():
        candidate = (_ADAPTER.cwd / candidate).resolve()
    if not candidate.exists() or not candidate.is_file():
        return candidate, "", f"Snapshot file not found: {candidate}"
    try:
        return candidate, candidate.read_text(encoding="utf-8"), None
    except OSError as exc:
        return candidate, "", f"Could not read snapshot file '{candidate}': {exc}"


def _task_page_observation_payload(
    *,
    page_id: str,
    route: dict[str, Any],
    baseline: dict[str, Any],
    page_data: dict[str, Any],
    snapshot_content: str,
    forms: list[dict[str, str]],
    tables: list[Any],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "page_id": page_id,
        "route": route,
        "baseline": baseline,
        "page_snapshot": {
            "source": "playwright-cli snapshot",
            "path": str(baseline.get("snapshot_path") or ""),
            "content": snapshot_content,
        },
        "forms": forms,
        "tables": _limited_list(tables, 20),
        "errors": errors,
    }


def _write_observation_index(
    destination: Path,
    *,
    worklist_file: Path,
    site_name: str,
    observation_dir: Path,
    observations: list[dict[str, Any]],
    observation_files: list[str],
    skipped: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    payload = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "worklist_path": str(worklist_file),
        "site_name": site_name,
        "observation_dir": str(observation_dir),
        "artifact_format": "yml",
        "summary": {
            "observation_count": len(observations),
            "skipped_count": len(skipped),
            "error_count": len(errors),
        },
        "observations": observations,
        "observation_files": observation_files,
        "skipped_routes": skipped,
        "errors": errors,
        "next_step": "Run Vertex-backed page summary and draft-case generation from page-*.yml artifacts.",
    }
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _task_observation_summary(path: Path, route: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    baseline = observation.get("baseline", {}) if isinstance(observation.get("baseline"), dict) else {}
    return {
        "path": str(path),
        "page_id": str(observation.get("page_id") or ""),
        "route_id": str(route.get("route_id") or ""),
        "route": str(route.get("canonical_path") or route.get("path") or "/"),
        "url": str(baseline.get("url") or ""),
        "title": str(baseline.get("title") or ""),
        "heading_count": len(baseline.get("headings", [])) if isinstance(baseline.get("headings"), list) else 0,
        "form_count": len(observation.get("forms", [])),
        "table_count": len(observation.get("tables", [])),
        "snapshot_line_count": len(str((observation.get("page_snapshot") or {}).get("content") or "").splitlines()),
        "error_count": len(observation.get("errors", [])),
    }


def _task_backlog_payload(
    *,
    drafts_file: Path,
    site_name: str,
    drafts: list[Any],
    tasks: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> dict[str, Any]:
    by_category = Counter(str(task["category"]) for task in tasks)
    by_risk = Counter(str(task["risk"]) for task in tasks)
    by_priority = Counter(str(task["priority"]) for task in tasks)
    by_policy = Counter(str(task["execution_policy"]) for task in tasks)
    return {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "source_drafts_path": str(drafts_file),
        "site_name": site_name,
        "summary": {
            "draft_count": len(drafts),
            "backlog_count": len(tasks),
            "skipped_count": len(skipped),
            "by_category": dict(sorted(by_category.items())),
            "by_risk": dict(sorted(by_risk.items())),
            "by_priority": dict(sorted(by_priority.items())),
            "by_execution_policy": dict(sorted(by_policy.items())),
        },
        "rules": {
            "priority": "Coverage value; delete/create/edit can be high priority.",
            "execution_order": "Risk-aware order; destructive/session-ending work runs last.",
            "human_review_gate": "Draft backlog is intended for human refinement before downstream execution.",
        },
        "tasks": tasks,
        "skipped_drafts": skipped,
    }


def _write_page_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    path.write_text(rendered, encoding="utf-8")


def _page_looks_like_auth_redirect(page_data: dict[str, Any]) -> bool:
    url = str(page_data.get("url") or "").lower()
    if any(marker in url for marker in ("/login", "/signin", "/sign-in", "/auth")):
        return True
    controls = page_data.get("controls", [])
    forms = page_data.get("forms", [])
    haystack = " ".join(
        [
            str(page_data.get("title") or ""),
            " ".join(_clean_strings(page_data.get("headings", []))),
            " ".join(str(item.get("label") or "") for item in controls if isinstance(item, dict)),
            " ".join(str(item.get("label") or item.get("name") or "") for item in forms if isinstance(item, dict)),
        ]
    ).lower()
    return "password" in haystack and any(word in haystack for word in ("login", "log in", "sign in", "email"))


def _clean_live_forms(values: Any) -> list[dict[str, str]]:
    forms: list[dict[str, str]] = []
    if not isinstance(values, list):
        return forms
    for item in values:
        if not isinstance(item, dict):
            continue
        forms.append(
            {
                "tag": _clean_text(str(item.get("tag") or "")),
                "type": _clean_text(str(item.get("type") or "")),
                "name": _clean_text(str(item.get("name") or "")),
                "label": _clean_text(str(item.get("label") or "")),
                "placeholder": _clean_text(str(item.get("placeholder") or "")),
                "aria_label": _clean_text(str(item.get("aria_label") or "")),
                "required": str(bool(item.get("required"))).lower(),
                "disabled": str(bool(item.get("disabled"))).lower(),
                "selector": _clean_text(str(item.get("selector") or "")),
            }
        )
    return forms


def _site_name_from_manifest(manifest: dict[str, Any]) -> str:
    options = manifest.get("crawl_options", {}) if isinstance(manifest.get("crawl_options"), dict) else {}
    for value in (
        options.get("site_name"),
        manifest.get("site_name"),
        manifest.get("base_origin"),
        manifest.get("start_url"),
    ):
        if value:
            return str(value)
    return "webapp"


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _route_is_unsafe(route: dict[str, Any]) -> bool:
    path = str(route.get("path") or route.get("url") or "")
    label = str(route.get("label") or "")
    text = " ".join([path, label]).lower()
    extra = {"download", "export", "import", "upload", "backup", "restore"}
    return (
        is_session_ending_ui_label(text)
        or any(keyword in text for keyword in DANGEROUS_UI_KEYWORDS)
        or any(keyword in text for keyword in extra)
    )


def _skipped_route(route: dict[str, Any], reason: str, route_key: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "route_id": str(route.get("id") or route.get("route_id") or ""),
        "route": route_key,
        "label": str(route.get("label") or ""),
    }


def _worklist_route_payload(
    route: dict[str, Any],
    *,
    site_name: str,
    base_origin: str,
    canonical_key: str,
    route_key: str,
) -> dict[str, Any]:
    path = _normalize_route_key(canonical_key)
    url = str(route.get("url") or "")
    selected_url = url if url else f"{base_origin.rstrip('/')}{path}" if base_origin else path
    route_id = str(route.get("id") or route.get("route_id") or f"{site_name}_{_slug(path)}")
    return {
        "route_id": route_id,
        "canonical_path": path,
        "route_key": canonical_key,
        "selected_route_key": route_key,
        "selected_url": selected_url,
        "label": str(route.get("label") or path),
        "page_type": str(route.get("page_type") or "page"),
        "phase": str(route.get("phase") or ""),
        "require_login": bool(route.get("require_login")),
        "navigation_steps": route.get("navigation_steps", []) if isinstance(route.get("navigation_steps"), list) else [],
        "folded_variants": [],
    }


def _decode_task_drafts(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        drafts = payload.get("drafts", payload.get("tasks", []))
        if isinstance(drafts, list):
            return drafts
    raise ValueError("task drafts must be a JSON array or an object with drafts.")


def _task_draft_site_name(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("site_name") or "")
    return ""


def _normalize_task_draft(draft: dict[str, Any], *, site_name: str, index: int) -> dict[str, Any]:
    route = _normalize_route_key(str(draft.get("route") or draft.get("entry_path") or "/"))
    goal = _clean_text(str(draft.get("goal") or draft.get("title") or draft.get("label") or "Task idea"))
    category = _normalize_task_category(str(draft.get("category") or draft.get("intent_type") or ""), goal)
    risk = _normalize_task_risk(str(draft.get("risk") or draft.get("safety_level") or ""), category, goal)
    priority = _normalize_task_priority(str(draft.get("priority") or ""), category, risk)
    execution_policy = _task_execution_policy(category, risk, draft)
    execution_order = _task_execution_order(category, risk, execution_policy, priority)
    entity = _clean_text(str(draft.get("entity") or _entity_from_goal(goal) or category))
    target = _clean_text(str(draft.get("target") or draft.get("control") or draft.get("label") or ""))
    dedupe_key = _task_dedupe_key(
        raw=str(draft.get("dedupe_key") or ""),
        site_name=site_name,
        category=category,
        entity=entity,
        route=route,
        target=target,
        goal=goal,
    )
    source_id = str(draft.get("draft_id") or draft.get("id") or f"draft_{index:03d}")
    backlog_id = _slug(str(draft.get("backlog_id") or f"{site_name}_{category}_{entity}_{route}_{index}"))
    return {
        "backlog_id": backlog_id,
        "source_draft_ids": [source_id],
        "dedupe_key": dedupe_key,
        "site_name": site_name,
        "route": route,
        "goal": goal,
        "category": category,
        "risk": risk,
        "priority": priority,
        "execution_order": execution_order,
        "execution_policy": execution_policy,
        "entity": entity,
        "target": target,
        "rough_steps": _clean_strings(draft.get("rough_steps", draft.get("steps", []))),
        "evidence": _clean_strings(draft.get("evidence", [])),
        "source_observations": _clean_strings(draft.get("source_observations", draft.get("observation_files", []))),
        "status": _task_backlog_status(risk, execution_policy),
        "requires_execution_repair": True,
    }


def _normalize_task_category(raw: str, goal: str) -> str:
    value = raw.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "remove": "delete",
        "destroy": "delete",
        "details": "open",
        "detail": "open",
        "view": "open",
        "session": "auth_session",
        "auth": "auth_session",
    }
    value = aliases.get(value, value)
    allowed = {"create", "edit", "delete", "filter", "search", "open", "navigate", "export", "import", "auth_session"}
    if value in allowed:
        return value
    text = goal.lower()
    if any(word in text for word in ("delete", "remove", "archive")):
        return "delete"
    if any(word in text for word in ("add", "create", "new")):
        return "create"
    if any(word in text for word in ("edit", "update", "save", "change")):
        return "edit"
    if any(word in text for word in ("filter", "date", "department", "status")):
        return "filter"
    if any(word in text for word in ("search", "find")):
        return "search"
    if any(word in text for word in ("open", "show", "view", "expand")):
        return "open"
    return "unknown"


def _normalize_task_risk(raw: str, category: str, goal: str) -> str:
    value = raw.strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {"read_only", "state_changing_safe", "state_changing_destructive", "session_ending", "external_side_effect", "unknown"}
    if value in allowed:
        return value
    text = f"{category} {goal}".lower()
    if is_session_ending_ui_label(text):
        return "session_ending"
    if category == "delete" or any(word in text for word in DANGEROUS_UI_KEYWORDS):
        return "state_changing_destructive"
    if category in {"create", "edit"}:
        return "state_changing_safe"
    if category in {"export", "import"}:
        return "external_side_effect"
    if category in {"filter", "search", "open", "navigate"}:
        return "read_only"
    return "unknown"


def _normalize_task_priority(raw: str, category: str, risk: str) -> str:
    value = raw.strip().upper()
    if value in {"P0", "P1", "P2", "P3"}:
        return value
    if category in {"create", "edit", "delete"}:
        return "P0"
    if category in {"filter", "search"}:
        return "P1"
    if category in {"open", "navigate"}:
        return "P2"
    if risk in {"state_changing_destructive", "session_ending"}:
        return "P0"
    return "P3"


def _task_execution_policy(category: str, risk: str, draft: dict[str, Any]) -> str:
    explicit = str(draft.get("execution_policy") or "").strip().lower()
    if explicit:
        return explicit
    if risk == "session_ending":
        return "manual_only"
    if category == "delete" or risk == "state_changing_destructive":
        return "dry_run_open_confirm"
    if category in {"create", "edit"}:
        if draft.get("test_data") or draft.get("depends_on"):
            return "execute_with_test_data"
        return "needs_test_data"
    if risk == "external_side_effect":
        return "draft_only"
    return "execute_read_only"


def _task_execution_order(category: str, risk: str, policy: str, priority: str) -> int:
    if risk == "session_ending" or policy == "manual_only":
        return 99
    if category == "delete" or risk == "state_changing_destructive":
        return 90
    if policy == "draft_only":
        return 80
    if category in {"create", "edit"}:
        return 40 if policy == "execute_with_test_data" else 60
    if category in {"filter", "search"}:
        return 20
    if category in {"open", "navigate"}:
        return 30
    return {"P0": 10, "P1": 20, "P2": 30, "P3": 70}.get(priority, 70)


def _task_backlog_status(risk: str, policy: str) -> str:
    if risk in {"session_ending", "state_changing_destructive"}:
        return "deferred_high_risk"
    if policy in {"needs_test_data", "draft_only"}:
        return "needs_review"
    return "ready_for_execution"


def _task_dedupe_key(
    *,
    raw: str,
    site_name: str,
    category: str,
    entity: str,
    route: str,
    target: str,
    goal: str,
) -> str:
    if raw.strip():
        return _normalize_text(raw)
    basis = "|".join([site_name, category, entity, route, target or goal])
    return _normalize_text(basis)


def _entity_from_goal(goal: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9 ]+", " ", goal).strip().lower()
    words = [word for word in normalized.split() if word not in {"the", "a", "an", "to", "by", "with", "new"}]
    return " ".join(words[:4])


def _merge_duplicate_task_draft(existing: dict[str, Any], duplicate: dict[str, Any]) -> None:
    existing["source_draft_ids"] = _dedupe(existing.get("source_draft_ids", []) + duplicate.get("source_draft_ids", []))
    existing["evidence"] = _dedupe(existing.get("evidence", []) + duplicate.get("evidence", []))[:20]
    existing["source_observations"] = _dedupe(
        existing.get("source_observations", []) + duplicate.get("source_observations", [])
    )[:20]
    if not existing.get("rough_steps") and duplicate.get("rough_steps"):
        existing["rough_steps"] = duplicate["rough_steps"]


def _skipped_task_draft(
    draft: dict[str, Any],
    reason: str,
    canonical_id: str | None = None,
) -> dict[str, Any]:
    skipped = {
        "reason": reason,
        "draft_id": str((draft.get("source_draft_ids") or [""])[0]),
        "goal": str(draft.get("goal") or ""),
        "category": str(draft.get("category") or ""),
        "route": str(draft.get("route") or ""),
    }
    if canonical_id:
        skipped["canonical_backlog_id"] = canonical_id
    return skipped


def _clean_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_clean_text(str(value)) for value in values if _clean_text(str(value))]


def _limited_list(values: Any, limit: int) -> list[Any]:
    if not isinstance(values, list):
        return []
    return values[: max(limit, 0)]


def _split_patterns(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _normalize_route_key(route_key: str) -> str:
    parsed = urlsplit(route_key or "/")
    return parsed.path or "/"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value).strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        result.append(key)
    return result


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value).strip()).strip("_").lower()
    return slug or "item"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _error_result(error: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message, **extra}
