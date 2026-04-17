"""Action intent extraction from route manifests."""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from adk_playwright_agent.app.policies import DANGEROUS_UI_KEYWORDS, resolve_workspace_path

_CREATE_KEYWORDS = {"add", "create", "new"}
_EDIT_KEYWORDS = {"edit", "update", "modify"}
_SEARCH_KEYWORDS = {"search", "find", "query", "lookup"}
_FILTER_KEYWORDS = {"date", "filter", "department", "status", "category", "type"}
_OPEN_KEYWORDS = {"open", "details", "show"}
_LOW_VALUE_ACTIONS = {"toggle navigation", "menu", "home"}
_HIGH_RISK_KEYWORDS = DANGEROUS_UI_KEYWORDS | {
    "approve",
    "backup",
    "download",
    "export",
    "import",
    "reject",
    "submit",
    "upload",
}
_GLOBAL_MODAL_FIELD_NAMES = {
    "from_date",
    "from_date_part",
    "leave_type",
    "reason",
    "redirect_back_to",
    "to_date",
    "to_date_part",
}
_INVALID_QUERY_MARKERS = {"=nan", "=undefined"}


def extract_action_intents_from_manifest(
    manifest_path: str,
    output_path: str,
    site_name: str | None = None,
    include_patterns: str | None = None,
    exclude_patterns: str | None = None,
    include_intent_types: str | None = None,
    exclude_intent_types: str | None = None,
    include_high_risk: bool = False,
    include_duplicate_skips: bool = False,
    min_confidence: float = 0.45,
    max_intents: int | None = None,
) -> dict[str, Any]:
    """Extract safe action intent metadata from a route manifest."""

    manifest_file = resolve_workspace_path(manifest_path)
    destination = resolve_workspace_path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    routes = manifest.get("routes", [])
    if not isinstance(routes, list):
        raise ValueError("manifest routes must be a JSON array.")

    inferred_site_name = _slug(site_name or _site_name_from_manifest(manifest))
    include = _split_patterns(include_patterns)
    exclude = _split_patterns(exclude_patterns)
    included_types = set(_split_patterns(include_intent_types))
    excluded_types = set(_split_patterns(exclude_intent_types))

    intents: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_dedupe_keys: set[str] = set()
    seen_intent_ids: set[str] = set()
    duplicate_count = 0

    for route in routes:
        if not isinstance(route, dict):
            skipped.append({"reason": "route_not_object"})
            continue

        route_key = _route_key(route)
        if include and not _matches_any(route_key, include):
            skipped.append({"reason": "include_pattern_mismatch", "route": route_key})
            continue
        if exclude and _matches_any(route_key, exclude):
            skipped.append({"reason": "exclude_pattern_match", "route": route_key})
            continue

        for candidate in _extract_route_candidates(route, inferred_site_name):
            intent_type = str(candidate["intent_type"])
            if included_types and intent_type not in included_types:
                skipped.append(_skip_candidate(candidate, "include_intent_type_mismatch"))
                continue
            if excluded_types and intent_type in excluded_types:
                skipped.append(_skip_candidate(candidate, "exclude_intent_type_match"))
                continue
            if not include_high_risk and candidate["safety_level"] == "high_risk":
                skipped.append(_skip_candidate(candidate, "high_risk"))
                continue
            if float(candidate["confidence"]) < min_confidence:
                skipped.append(_skip_candidate(candidate, "low_confidence"))
                continue

            dedupe_key = _dedupe_key(candidate)
            if dedupe_key in seen_dedupe_keys:
                duplicate_count += 1
                if include_duplicate_skips:
                    skipped.append(_skip_candidate(candidate, "duplicate"))
                continue
            seen_dedupe_keys.add(dedupe_key)

            candidate["intent_id"] = _unique_intent_id(candidate["intent_id"], seen_intent_ids)
            intents.append(candidate)
            if max_intents is not None and len(intents) >= max_intents:
                break

        if max_intents is not None and len(intents) >= max_intents:
            break

    by_type = Counter(intent["intent_type"] for intent in intents)
    by_safety = Counter(intent["safety_level"] for intent in intents)
    payload = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "manifest_path": str(manifest_file),
        "site_name": inferred_site_name,
        "summary": {
            "intent_count": len(intents),
            "skipped_count": len(skipped),
            "duplicate_count": duplicate_count,
            "by_type": dict(sorted(by_type.items())),
            "by_safety_level": dict(sorted(by_safety.items())),
        },
        "intents": intents,
        "skipped_candidates": skipped,
    }
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "manifest_path": str(manifest_file),
        "output_path": str(destination),
        "site_name": inferred_site_name,
        "intent_count": len(intents),
        "skipped_count": len(skipped),
        "duplicate_count": duplicate_count,
        "by_type": payload["summary"]["by_type"],
        "by_safety_level": payload["summary"]["by_safety_level"],
    }


def _extract_route_candidates(route: dict[str, Any], site_name: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    context = route.get("context", {}) if isinstance(route.get("context"), dict) else {}
    actions = _clean_strings(context.get("primary_actions", []))
    headings = _clean_strings(context.get("headings", []))
    forms = _clean_forms(context.get("forms", []))

    route_text = _route_text(route, headings)
    route_id = str(route.get("id") or _slug(_route_key(route)))
    route_key = _route_key(route)
    if _route_has_invalid_query(route_key):
        return []

    page_candidate = _candidate_from_text(
        text=route_text,
        label=str(route.get("label") or route_key),
        route=route,
        site_name=site_name,
        route_id=route_id,
        entry_path=route_key,
        fields=_form_field_names(forms),
        source="route",
    )
    if page_candidate:
        candidates.append(page_candidate)

    new_fields = _new_form_field_names(forms)
    if new_fields:
        entity = _infer_entity(route=route, label=str(route.get("label") or route_key), intent_type="create")
        grouped_create = _candidate_from_text(
            text=f"new {entity} create {' '.join(new_fields)}",
            label=f"New {entity}",
            route=route,
            site_name=site_name,
            route_id=route_id,
            entry_path=route_key,
            fields=new_fields,
            source="form_group",
        )
        if grouped_create:
            candidates.append(grouped_create)

    for form in forms:
        if (
            _form_is_hidden(form)
            or _form_is_global_modal_field(form)
            or _form_is_inline_new_field(form)
        ):
            continue
        form_text = " ".join(str(value) for value in form.values() if value)
        form_candidate = _candidate_from_text(
            text=form_text,
            label=_form_label(form),
            route=route,
            site_name=site_name,
            route_id=route_id,
            entry_path=route_key,
            fields=_form_field_names([form]),
            source="form",
        )
        if (
            form_candidate
            and form_candidate["intent_type"] == "filter"
            and _route_is_mutation_page(route)
        ):
            continue
        if (
            form_candidate
            and form_candidate["intent_type"] == "filter"
            and not _form_supports_filter(route, form)
        ):
            continue
        if form_candidate:
            candidates.append(form_candidate)

    for action in actions:
        if action.lower() in _LOW_VALUE_ACTIONS:
            continue
        action_candidate = _candidate_from_text(
            text=action,
            label=action,
            route=route,
            site_name=site_name,
            route_id=route_id,
            entry_path=route_key,
            fields=[],
            source="primary_action",
        )
        if action_candidate:
            candidates.append(action_candidate)

    return candidates


def _candidate_from_text(
    *,
    text: str,
    label: str,
    route: dict[str, Any],
    site_name: str,
    route_id: str,
    entry_path: str,
    fields: list[str],
    source: str,
) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    safety_level = _safety_level(normalized)
    intent_type = _intent_type(normalized)
    if intent_type is None and safety_level == "high_risk":
        intent_type = "high_risk_action"
    if source == "route" and intent_type == "open":
        return None
    if source == "route" and intent_type in {"create", "edit"}:
        if not _route_supports_mutation_intent(route, intent_type):
            return None
    if intent_type is None:
        return None

    entity = _infer_entity(route=route, label=label, intent_type=intent_type)
    confidence = _confidence(intent_type=intent_type, source=source, fields=fields, text=normalized)
    submit_label = _submit_label(intent_type)
    success = _success_assertions(intent_type=intent_type, entity=entity, label=label)
    intent_id = "_".join(
        [
            _slug(site_name),
            _slug(str(route.get("phase") or "route")),
            _slug(intent_type),
            _slug(entity),
            _slug(entry_path),
        ]
    )

    return {
        "intent_id": intent_id,
        "route_id": route_id,
        "intent_type": intent_type,
        "label": label,
        "entity": entity,
        "entry_path": entry_path,
        "phase": str(route.get("phase") or ""),
        "require_login": bool(route.get("require_login")),
        "input_fields": fields,
        "submit_control": submit_label,
        "success_evidence": success,
        "safety_level": safety_level,
        "confidence": confidence,
        "source": {
            "kind": source,
            "route_path": str(route.get("path") or "/"),
            "route_label": str(route.get("label") or ""),
            "evidence": text[:240],
        },
    }


def _intent_type(text: str) -> str | None:
    words = set(re.findall(r"[a-z0-9]+", text))
    if words & _SEARCH_KEYWORDS:
        return "search"
    if words & _CREATE_KEYWORDS:
        return "create"
    if words & _EDIT_KEYWORDS:
        return "edit"
    if words & _FILTER_KEYWORDS:
        return "filter"
    if words & _OPEN_KEYWORDS:
        return "open"
    return None


def _safety_level(text: str) -> str:
    if any(keyword in text for keyword in _HIGH_RISK_KEYWORDS):
        return "high_risk"
    intent_type = _intent_type(text)
    if intent_type in {"search", "filter", "open"}:
        return "read_only"
    return "safe_with_confirmation"


def _confidence(intent_type: str, source: str, fields: list[str], text: str) -> float:
    confidence = 0.55
    if source == "route":
        confidence += 0.15
    if source == "form":
        confidence += 0.20
    if fields:
        confidence += 0.10
    if intent_type in {"search", "filter"} and any(field in text for field in fields):
        confidence += 0.05
    if intent_type == "high_risk_action":
        confidence -= 0.15
    return round(min(max(confidence, 0.0), 0.95), 2)


def _submit_label(intent_type: str) -> str | None:
    if intent_type == "search":
        return "Search"
    if intent_type == "filter":
        return "Apply filter"
    if intent_type == "create":
        return "Create"
    if intent_type == "edit":
        return "Save"
    return None


def _success_assertions(intent_type: str, entity: str, label: str) -> list[str]:
    if intent_type == "search":
        return ["Search results or filtered rows should be visible."]
    if intent_type == "filter":
        return ["The page should show results matching the selected filter."]
    if intent_type == "create":
        return [
            f"A new {entity} should be created or a confirmation should be visible.",
            "The workflow should not show validation errors for valid input.",
        ]
    if intent_type == "edit":
        return [
            f"The {entity} changes should be saved or a confirmation should be visible.",
            "The workflow should not show validation errors for valid input.",
        ]
    if intent_type == "open":
        return [f'The "{label}" UI should become visible.']
    return ["The intended UI outcome should be visible."]


def _infer_entity(route: dict[str, Any], label: str, intent_type: str) -> str:
    path = str(route.get("path") or "")
    parts = [_singularize(part) for part in path.split("/") if part and not part.isdigit()]
    generic = {
        "add",
        "create",
        "edit",
        "new",
        "settings",
        "search",
        "filter",
        "import",
    }
    for part in reversed(parts):
        if part not in generic:
            return part
    words = [word for word in re.findall(r"[a-zA-Z0-9]+", label.lower()) if word not in generic]
    if words:
        return _singularize(words[-1])
    return "item" if intent_type in {"create", "edit"} else "result"


def _route_supports_mutation_intent(route: dict[str, Any], intent_type: str) -> bool:
    page_type = str(route.get("page_type") or "").lower()
    if page_type == intent_type:
        return True

    path = str(route.get("path") or "")
    meaningful_segments = [
        segment.lower()
        for segment in path.split("/")
        if segment and not segment.isdigit()
    ]
    if not meaningful_segments:
        return False
    last_segment = meaningful_segments[-1]
    if intent_type == "create":
        return last_segment in _CREATE_KEYWORDS
    if intent_type == "edit":
        return last_segment in _EDIT_KEYWORDS
    return False


def _route_is_mutation_page(route: dict[str, Any]) -> bool:
    return _route_supports_mutation_intent(route, "create") or _route_supports_mutation_intent(
        route, "edit"
    )


def _form_is_hidden(form: dict[str, str]) -> bool:
    return str(form.get("type") or "").strip().lower() == "hidden"


def _form_is_global_modal_field(form: dict[str, str]) -> bool:
    name = str(form.get("name") or "").strip().lower()
    return name in _GLOBAL_MODAL_FIELD_NAMES


def _form_is_inline_new_field(form: dict[str, str]) -> bool:
    name = str(form.get("name") or "").strip().lower()
    return name.endswith("__new")


def _form_supports_filter(route: dict[str, Any], form: dict[str, str]) -> bool:
    name = str(form.get("name") or "").strip().lower()
    path = str(route.get("path") or "").lower()
    if name in {"start_date", "end_date", "user_id"}:
        return path.startswith("/audit") or path.startswith("/reports")
    if name == "department":
        return path.startswith("/reports") or path.startswith("/calendar/teamview")
    if name in {"status", "category"}:
        return True
    return False


def _form_field_names(forms: list[dict[str, str]]) -> list[str]:
    names: list[str] = []
    for form in forms:
        for key in ("name", "placeholder", "aria_label", "type"):
            value = str(form.get(key) or "").strip()
            if value and value.lower() not in {"text", "button", "submit", "hidden"}:
                names.append(value)
    return _dedupe(names)[:20]


def _new_form_field_names(forms: list[dict[str, str]]) -> list[str]:
    names: list[str] = []
    for form in forms:
        name = str(form.get("name") or "").strip()
        if name.lower().endswith("__new"):
            names.append(name)
    return _dedupe(names)[:20]


def _form_label(form: dict[str, str]) -> str:
    return (
        str(form.get("placeholder") or form.get("aria_label") or form.get("name") or form.get("type") or "Form")
    )


def _route_text(route: dict[str, Any], headings: list[str]) -> str:
    return " ".join(
        [
            str(route.get("label") or ""),
            str(route.get("path") or ""),
            str(route.get("page_type") or ""),
            " ".join(headings),
        ]
    )


def _clean_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return _dedupe(str(value).strip() for value in values if str(value).strip())


def _clean_forms(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    forms: list[dict[str, str]] = []
    for value in values:
        if isinstance(value, dict):
            forms.append({str(key): str(raw).strip() for key, raw in value.items() if str(raw).strip()})
    return forms


def _skip_candidate(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "route": candidate.get("entry_path"),
        "intent_type": candidate.get("intent_type"),
        "label": candidate.get("label"),
        "safety_level": candidate.get("safety_level"),
        "confidence": candidate.get("confidence"),
    }


def _dedupe_key(candidate: dict[str, Any]) -> str:
    source = candidate.get("source", {}) if isinstance(candidate.get("source"), dict) else {}
    source_kind = str(source.get("kind") or "")
    if source_kind == "primary_action":
        return "|".join(
            [
                "primary_action",
                str(candidate.get("intent_type") or ""),
                _normalize_text(str(candidate.get("label") or "")),
            ]
        )
    if source_kind == "form" and candidate.get("intent_type") == "filter":
        return "|".join(
            [
                "form_filter",
                str(candidate.get("entry_path") or "").split("?", 1)[0],
                _normalize_text(str(candidate.get("label") or "")),
            ]
        )
    return "|".join(
        [
            str(candidate.get("route_id") or ""),
            str(candidate.get("intent_type") or ""),
            _normalize_text(str(candidate.get("label") or "")),
            str(candidate.get("entry_path") or ""),
            source_kind,
        ]
    )


def _route_key(route: dict[str, Any]) -> str:
    path = str(route.get("path") or "/")
    query = str(route.get("query") or "")
    return f"{path}?{query}" if query else path


def _route_has_invalid_query(route_key: str) -> bool:
    normalized = route_key.lower()
    return any(marker in normalized for marker in _INVALID_QUERY_MARKERS)


def _site_name_from_manifest(manifest: dict[str, Any]) -> str:
    options = manifest.get("crawl_options", {}) if isinstance(manifest.get("crawl_options"), dict) else {}
    for value in (options.get("site_name"), manifest.get("site_name"), manifest.get("base_origin"), manifest.get("start_url")):
        if value:
            return str(value)
    return "webapp"


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _split_patterns(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _singularize(value: str) -> str:
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("s") and not value.endswith("ss") and len(value) > 3:
        return value[:-1]
    return value


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value).strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        result.append(key)
    return result


def _unique_intent_id(intent_id: str, seen: set[str]) -> str:
    candidate = intent_id
    index = 2
    while candidate in seen:
        candidate = f"{intent_id}_{index}"
        index += 1
    seen.add(candidate)
    return candidate


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or "item"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
