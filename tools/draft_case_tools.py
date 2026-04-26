"""Vertex-backed draft case generation and page-draft merging tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from adk_playwright_agent.adapters.vertex_genai import VertexGenAIAdapter
from adk_playwright_agent.app.policies import resolve_workspace_path
from adk_playwright_agent.app.page_artifact_prompting import compact_page_artifact_for_prompt
from adk_playwright_agent.app.vertex_prompts import DRAFT_TEST_CASES_PROMPT
from adk_playwright_agent.tools.action_task_tools import consolidate_task_drafts_to_backlog

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard
    yaml = None

_VERTEX = VertexGenAIAdapter()
_EXCLUDED_DRAFT_CATEGORIES = {"navigate", "open", "export", "import", "unknown"}


def draft_test_ideas_with_vertex(
    observation_index_path: str,
    draft_index_path: str,
    draft_output_dir: str | None = None,
    summary_index_path: str | None = None,
    site_name: str | None = None,
    max_pages: int | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Generate page-level draft cases from observation artifacts using Vertex AI."""

    observation_index_file = resolve_workspace_path(observation_index_path)
    draft_index_file = resolve_workspace_path(draft_index_path)
    if not observation_index_file.exists() or not observation_index_file.is_file():
        return _error("file_not_found", f"File not found: {observation_index_file}", observation_index_path=str(observation_index_file))

    try:
        observation_index = json.loads(observation_index_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error("invalid_json", f"Invalid JSON in '{observation_index_file}': {exc}", observation_index_path=str(observation_index_file))

    observations = observation_index.get("observations", [])
    if not isinstance(observations, list):
        return _error("invalid_index_schema", "observations must be a JSON array.", observation_index_path=str(observation_index_file))

    draft_root = resolve_workspace_path(draft_output_dir or str(draft_index_file.parent / "page_drafts"))
    draft_root.mkdir(parents=True, exist_ok=True)
    draft_index_file.parent.mkdir(parents=True, exist_ok=True)

    summaries_by_page = _load_summary_index(summary_index_path)
    inferred_site_name = str(site_name or observation_index.get("site_name") or "webapp")
    generated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for index, observation in enumerate(observations, start=1):
        if max_pages is not None and len(generated) >= max_pages:
            skipped.append({"reason": "max_pages_reached", "index": index})
            continue
        if not isinstance(observation, dict):
            skipped.append({"reason": "observation_not_object", "index": index})
            continue

        page_path = resolve_workspace_path(str(observation.get("path") or ""))
        if not page_path.exists() or not page_path.is_file():
            errors.append({"reason": "page_artifact_missing", "path": str(page_path)})
            continue

        page_payload = _load_page_artifact(page_path)
        if not isinstance(page_payload, dict):
            errors.append({"reason": "invalid_page_artifact", "path": str(page_path)})
            continue

        page_id = str(page_payload.get("page_id") or observation.get("page_id") or f"page-{index:03d}")
        summary_payload = summaries_by_page.get(page_id, {})
        output_path = draft_root / f"{page_id}.drafts.json"
        if output_path.exists() and not overwrite:
            skipped.append({"reason": "exists", "path": str(output_path), "page_id": page_id})
            generated.append({"page_id": page_id, "path": str(output_path), "source_page": str(page_path), "cached": True})
            continue

        prompt = _draft_prompt(inferred_site_name, page_payload, summary_payload)
        result = _VERTEX.generate_json(prompt=prompt, temperature=0.4, max_output_tokens=4096)
        if not result.get("ok"):
            errors.append({"reason": str(result.get("error") or "vertex_failed"), "message": str(result.get("message") or ""), "page_id": page_id, "path": str(page_path)})
            continue

        draft_payload = _normalize_page_drafts(
            site_name=inferred_site_name,
            page_payload=page_payload,
            summary_payload=summary_payload,
            model_payload=result["data"],
        )
        output_path.write_text(json.dumps(draft_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        generated.append(
            {
                "page_id": page_id,
                "route": str(draft_payload.get("route") or ""),
                "path": str(output_path),
                "source_page": str(page_path),
                "draft_count": len(draft_payload.get("drafts", [])),
            }
        )

    draft_index_payload = {
        "schema_version": "1.0",
        "observation_index_path": str(observation_index_file),
        "summary_index_path": str(resolve_workspace_path(summary_index_path)) if summary_index_path else "",
        "site_name": inferred_site_name,
        "draft_dir": str(draft_root),
        "draft_page_count": len(generated),
        "error_count": len(errors),
        "skipped_count": len(skipped),
        "draft_pages": generated,
        "errors": errors,
        "skipped": skipped,
    }
    draft_index_file.write_text(json.dumps(draft_index_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": not errors,
        "observation_index_path": str(observation_index_file),
        "draft_index_path": str(draft_index_file),
        "draft_output_dir": str(draft_root),
        "site_name": inferred_site_name,
        "draft_page_count": len(generated),
        "error_count": len(errors),
        "skipped_count": len(skipped),
    }


def merge_page_drafts(
    draft_index_path: str,
    output_path: str,
    site_name: str | None = None,
    include_categories: str | None = None,
    exclude_categories: str | None = None,
    max_drafts: int | None = None,
) -> dict[str, Any]:
    """Merge page-level draft files into one normalized draft backlog."""

    draft_index_file = resolve_workspace_path(draft_index_path)
    destination = resolve_workspace_path(output_path)
    if not draft_index_file.exists() or not draft_index_file.is_file():
        return _error("file_not_found", f"File not found: {draft_index_file}", draft_index_path=str(draft_index_file))

    try:
        draft_index = json.loads(draft_index_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error("invalid_json", f"Invalid JSON in '{draft_index_file}': {exc}", draft_index_path=str(draft_index_file))

    draft_pages = draft_index.get("draft_pages", [])
    if not isinstance(draft_pages, list):
        return _error("invalid_index_schema", "draft_pages must be a JSON array.", draft_index_path=str(draft_index_file))

    raw_drafts: list[dict[str, Any]] = []
    page_count = 0
    for item in draft_pages:
        if not isinstance(item, dict):
            continue
        draft_file = resolve_workspace_path(str(item.get("path") or ""))
        if not draft_file.exists() or not draft_file.is_file():
            continue
        try:
            payload = json.loads(draft_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        drafts = payload.get("drafts", [])
        if not isinstance(drafts, list):
            continue
        page_count += 1
        raw_drafts.extend(drafts)

    raw_payload = {
        "site_name": str(site_name or draft_index.get("site_name") or "webapp"),
        "drafts": raw_drafts,
    }
    temp_raw_file = destination.with_suffix(".raw.json")
    temp_raw_file.parent.mkdir(parents=True, exist_ok=True)
    temp_raw_file.write_text(json.dumps(raw_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    backlog_result = consolidate_task_drafts_to_backlog(
        drafts_path=str(temp_raw_file),
        output_path=str(destination),
        site_name=str(site_name or draft_index.get("site_name") or "webapp"),
        max_tasks=max_drafts,
        include_categories=include_categories,
        exclude_categories=_merge_exclude_categories(exclude_categories),
    )
    if not backlog_result.get("ok"):
        return backlog_result

    try:
        backlog_payload = json.loads(destination.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error("invalid_generated_backlog", f"Invalid JSON in '{destination}': {exc}", output_path=str(destination))

    backlog_payload["source_draft_index_path"] = str(draft_index_file)
    backlog_payload["summary"] = {
        **(backlog_payload.get("summary", {}) if isinstance(backlog_payload.get("summary"), dict) else {}),
        "page_count": page_count,
        "raw_draft_count": len(raw_drafts),
    }
    destination.write_text(json.dumps(backlog_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        **backlog_result,
        "page_count": page_count,
        "raw_draft_count": len(raw_drafts),
    }


def _draft_prompt(site_name: str, page_payload: dict[str, Any], summary_payload: dict[str, Any]) -> str:
    prompt_payload = compact_page_artifact_for_prompt(
        page_payload,
        max_forms=24,
        max_tables=12,
        snapshot_head_lines=140,
        snapshot_tail_lines=35,
        snapshot_char_limit=12000,
    )
    parts = [
        DRAFT_TEST_CASES_PROMPT,
        f"SITE_NAME: {site_name}",
        "PAGE_ARTIFACT:",
        _render_page_payload(prompt_payload),
    ]
    if summary_payload:
        parts.extend(
            [
                "PAGE_SUMMARY:",
                json.dumps(summary_payload, ensure_ascii=False, indent=2),
            ]
        )
    return "\n\n".join(parts)


def _normalize_page_drafts(
    *,
    site_name: str,
    page_payload: dict[str, Any],
    summary_payload: dict[str, Any],
    model_payload: dict[str, Any],
) -> dict[str, Any]:
    route = page_payload.get("route", {}) if isinstance(page_payload.get("route"), dict) else {}
    drafts = model_payload.get("drafts", [])
    normalized: list[dict[str, Any]] = []
    if isinstance(drafts, list):
        for index, draft in enumerate(drafts, start=1):
            if not isinstance(draft, dict):
                continue
            title = str(draft.get("title") or "").strip()
            goal = str(draft.get("goal") or title or "Draft test case").strip()
            category = _normalize_enum(str(draft.get("category") or ""), {"create", "edit", "delete", "filter", "search"}, "")
            priority = _normalize_enum(str(draft.get("priority") or "P2").upper(), {"P0", "P1", "P2", "P3"}, "P2")
            risk = _normalize_enum(str(draft.get("risk") or "unknown"), {"read_only", "state_changing_safe", "state_changing_destructive", "session_ending", "external_side_effect", "unknown"}, "unknown")
            draft_id = str(draft.get("draft_id") or f"{site_name}_{_slug(category)}_{_slug(goal)}_{index:02d}")
            if not category or category in _EXCLUDED_DRAFT_CATEGORIES:
                continue
            normalized.append(
                {
                    "draft_id": draft_id,
                    "route": str(route.get("canonical_path") or route.get("path") or "/"),
                    "title": title or goal,
                    "goal": goal,
                    "category": category,
                    "priority": priority,
                    "risk": risk,
                    "rough_steps": _string_list(draft.get("rough_steps")),
                    "evidence": _string_list(draft.get("evidence")),
                    "notes_for_human": _string_list(draft.get("notes_for_human")),
                    "source_observations": [str(page_payload.get("_source_path") or "")],
                    "source_summary": str(summary_payload.get("source_page_artifact") or summary_payload.get("page_id") or ""),
                    "dedupe_key": str(draft.get("dedupe_key") or f"{site_name}|{category}|{route.get('canonical_path') or route.get('path') or '/'}|{_normalize_text(title or goal)}"),
                }
            )
    normalized = _reduce_input_variants(page_payload=page_payload, drafts=normalized)
    baseline = page_payload.get("baseline", {}) if isinstance(page_payload.get("baseline"), dict) else {}
    return {
        "schema_version": "1.0",
        "page_id": str(page_payload.get("page_id") or ""),
        "route": str(route.get("canonical_path") or route.get("path") or "/"),
        "url": str(baseline.get("url") or ""),
        "title": str(baseline.get("title") or ""),
        "navigation_steps": _string_list(route.get("navigation_steps")),
        "plain_language_summary": str(summary_payload.get("plain_language_summary") or ""),
        "drafts": normalized,
        "source_page_artifact": str(page_payload.get("_source_path") or ""),
    }


def _load_summary_index(summary_index_path: str | None) -> dict[str, dict[str, Any]]:
    if not summary_index_path:
        return {}
    summary_index_file = resolve_workspace_path(summary_index_path)
    if not summary_index_file.exists() or not summary_index_file.is_file():
        return {}
    try:
        payload = json.loads(summary_index_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    summaries = payload.get("summaries", [])
    if not isinstance(summaries, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in summaries:
        if not isinstance(item, dict):
            continue
        summary_path = resolve_workspace_path(str(item.get("path") or ""))
        if not summary_path.exists() or not summary_path.is_file():
            continue
        try:
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(summary_payload, dict):
            result[str(summary_payload.get("page_id") or item.get("page_id") or "")] = summary_payload
    return result


def _load_page_artifact(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        try:
            decoded = yaml.safe_load(text)
            if isinstance(decoded, dict):
                decoded["_source_path"] = str(path)
                return decoded
        except Exception:
            pass
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(decoded, dict):
        decoded["_source_path"] = str(path)
        return decoded
    return None


def _render_page_payload(payload: dict[str, Any]) -> str:
    if yaml is not None:
        return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _normalize_enum(value: str, allowed: set[str], fallback: str) -> str:
    candidate = value.strip().lower().replace("-", "_").replace(" ", "_")
    if candidate in allowed:
        return candidate
    upper_candidate = value.strip().upper()
    if upper_candidate in allowed:
        return upper_candidate
    return fallback


def _merge_exclude_categories(exclude_categories: str | None) -> str:
    merged = list(_EXCLUDED_DRAFT_CATEGORIES)
    if exclude_categories:
        merged.extend(part.strip() for part in exclude_categories.split(",") if part.strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for item in merged:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return ",".join(deduped)


def _reduce_input_variants(*, page_payload: dict[str, Any], drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    forms = page_payload.get("forms", []) if isinstance(page_payload.get("forms"), list) else []
    if not forms or len(drafts) <= 1:
        return drafts

    passthrough: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    route = str(
        ((page_payload.get("route") or {}) if isinstance(page_payload.get("route"), dict) else {}).get("canonical_path")
        or ((page_payload.get("route") or {}) if isinstance(page_payload.get("route"), dict) else {}).get("path")
        or "/"
    )
    search_like_group_allowed = len(forms) <= 2

    for draft in drafts:
        category = str(draft.get("category") or "")
        if category in {"create", "edit"}:
            grouped.setdefault((route, category), []).append(draft)
            continue
        if category in {"search", "filter"} and search_like_group_allowed:
            grouped.setdefault((route, category), []).append(draft)
            continue
        passthrough.append(draft)

    reduced = list(passthrough)
    for grouped_drafts in grouped.values():
        reduced.append(_select_preferred_input_draft(grouped_drafts, forms=forms))
    return reduced


def _select_preferred_input_draft(drafts: list[dict[str, Any]], *, forms: list[Any]) -> dict[str, Any]:
    return max(drafts, key=lambda draft: _input_draft_score(draft, forms=forms))


def _input_draft_score(draft: dict[str, Any], *, forms: list[Any]) -> tuple[int, int, int, int]:
    text_parts = [
        str(draft.get("title") or ""),
        str(draft.get("goal") or ""),
        " ".join(_string_list(draft.get("rough_steps"))),
        " ".join(_string_list(draft.get("evidence"))),
        " ".join(_string_list(draft.get("notes_for_human"))),
    ]
    haystack = _normalize_text(" ".join(text_parts))
    positive_terms = (
        "valid",
        "happy path",
        "success",
        "successfully",
        "existing",
        "known",
        "all fields",
        "all available fields",
        "required fields",
    )
    negative_terms = (
        "invalid",
        "empty",
        "blank",
        "non existent",
        "non-existent",
        "without",
        "prevent",
        "error",
        "fail",
        "optional only",
        "only first name",
        "minimal",
    )
    sentiment_score = sum(4 for term in positive_terms if term in haystack) - sum(
        6 for term in negative_terms if term in haystack
    )
    form_coverage_score = _form_coverage_score(haystack, forms=forms)
    evidence_score = len(_string_list(draft.get("evidence")))
    step_score = len(_string_list(draft.get("rough_steps")))
    return (sentiment_score, form_coverage_score, evidence_score, step_score)


def _form_coverage_score(haystack: str, *, forms: list[Any]) -> int:
    score = 0
    for form in forms:
        if not isinstance(form, dict):
            continue
        for candidate in (
            str(form.get("label") or "").strip(),
            str(form.get("name") or "").strip(),
            str(form.get("placeholder") or "").strip(),
        ):
            normalized = _normalize_text(candidate)
            if normalized and normalized in haystack:
                score += 1
                break
    return score


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower() or "item"


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message, **extra}
