"""Action intent extraction from route manifests."""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from adk_playwright_agent.adapters.playwright_cli import PlaywrightCliAdapter
from adk_playwright_agent.app.policies import (
    DANGEROUS_UI_KEYWORDS,
    is_session_ending_ui_label,
    resolve_workspace_path,
)

_ADAPTER = PlaywrightCliAdapter()

_CREATE_KEYWORDS = {"add", "create", "new"}
_EDIT_KEYWORDS = {"edit", "modify", "save", "update"}
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
    "})).slice(0, 80);"
    "const controls = [...document.querySelectorAll('button,[role=\"button\"],a[href],input[type=\"submit\"],summary')].filter(visible).map(el => {"
    "  const url = attr(el, 'href') ? safeUrl(attr(el, 'href')) : null;"
    "  return { label: text(el), tag: el.tagName.toLowerCase(), role: attr(el, 'role'), type: attr(el, 'type'),"
    "    href: url ? url.href : '', path: url ? url.pathname : '', query: url ? url.search.slice(1) : '',"
    "    same_origin: url ? url.origin === location.origin : true, disabled: !!el.disabled || attr(el, 'aria-disabled') === 'true', selector: selectorFor(el) };"
    "}).filter(x => x.label || x.href).slice(0, 120);"
    "const headings = [...document.querySelectorAll('h1,h2,h3,[role=\"heading\"]')].filter(visible).map(text).filter(Boolean).slice(0, 20);"
    "const tables = [...document.querySelectorAll('table,[role=\"table\"],.table')].filter(visible).map(el => ({ text: text(el).slice(0, 240) })).slice(0, 10);"
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
    """Build a canonical route worklist for browser-backed action discovery."""

    manifest_file = resolve_workspace_path(manifest_path)
    destination = resolve_workspace_path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    routes = manifest.get("routes", [])
    if not isinstance(routes, list):
        raise ValueError("manifest routes must be a JSON array.")

    inferred_site_name = _slug(site_name or _site_name_from_manifest(manifest))
    base_origin = str(manifest.get("base_origin") or manifest.get("start_url") or "").rstrip("/")
    include = _split_patterns(include_patterns)
    exclude = _split_patterns(exclude_patterns)
    skipped: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}

    for route in routes:
        if not isinstance(route, dict):
            skipped.append({"reason": "route_not_object"})
            continue

        route_key = _normalize_route_key(_route_key(route))
        canonical_key = _canonical_action_route_key(route_key, skip_query_variants=skip_query_variants)
        skip_reason = _action_route_skip_reason(
            route=route,
            route_key=route_key,
            canonical_key=canonical_key,
            include_patterns=include,
            exclude_patterns=exclude,
            include_unsafe_routes=include_unsafe_routes,
        )
        if skip_reason:
            skipped.append(
                {
                    "reason": skip_reason,
                    "route": route_key,
                    "canonical_path": canonical_key,
                    "label": str(route.get("label") or ""),
                    "route_id": str(route.get("id") or ""),
                }
            )
            continue
        groups.setdefault(canonical_key, []).append(route)

    worklist_routes: list[dict[str, Any]] = []
    folded_variant_count = 0
    seen_worklist_ids: set[str] = set()

    for canonical_key, grouped_routes in groups.items():
        if max_routes is not None and len(worklist_routes) >= max_routes:
            for route in grouped_routes:
                skipped.append(
                    {
                        "reason": "max_routes_reached",
                        "route": _normalize_route_key(_route_key(route)),
                        "canonical_path": canonical_key,
                        "label": str(route.get("label") or ""),
                        "route_id": str(route.get("id") or ""),
                    }
                )
            continue

        selected = _select_canonical_route(grouped_routes, canonical_key)
        source_route_keys = [_normalize_route_key(_route_key(route)) for route in grouped_routes]
        folded_variants = [
            route_key
            for route_key in source_route_keys
            if skip_query_variants and route_key != canonical_key
        ]
        folded_variant_count += len(folded_variants)
        route_id = str(selected.get("id") or _slug(canonical_key))
        worklist_id = _unique_intent_id(
            f"{inferred_site_name}_action_route_{_slug(canonical_key)}",
            seen_worklist_ids,
        )
        selected_url = _canonical_route_url(base_origin, canonical_key, selected)
        worklist_routes.append(
            {
                "worklist_id": worklist_id,
                "route_id": route_id,
                "canonical_path": canonical_key,
                "selected_url": selected_url,
                "selected_route_key": _normalize_route_key(_route_key(selected)),
                "selected_from_query_variant": (
                    skip_query_variants and _normalize_route_key(_route_key(selected)) != canonical_key
                ),
                "label": str(selected.get("label") or canonical_key),
                "page_type": str(selected.get("page_type") or ""),
                "phase": str(selected.get("phase") or ""),
                "require_login": bool(selected.get("require_login")),
                "source_path": selected.get("source_path"),
                "navigation_steps": selected.get("navigation_steps") or [],
                "assertions": selected.get("assertions") or [],
                "folded_variants": folded_variants,
                "source_route_ids": [
                    str(route.get("id") or _slug(_normalize_route_key(_route_key(route))))
                    for route in grouped_routes
                ],
            }
        )

    payload = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "manifest_path": str(manifest_file),
        "site_name": inferred_site_name,
        "base_origin": base_origin,
        "start_url": str(manifest.get("start_url") or ""),
        "options": {
            "skip_query_variants": skip_query_variants,
            "include_unsafe_routes": include_unsafe_routes,
            "include_patterns": include,
            "exclude_patterns": exclude,
            "max_routes": max_routes,
        },
        "summary": {
            "source_route_count": len(routes),
            "canonical_route_count": len(worklist_routes),
            "folded_variant_count": folded_variant_count,
            "skipped_count": len(skipped),
        },
        "routes": worklist_routes,
        "skipped_routes": skipped,
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
        "canonical_route_count": len(worklist_routes),
        "folded_variant_count": folded_variant_count,
        "skipped_count": len(skipped),
    }


def discover_page_actions_from_worklist(
    worklist_path: str,
    output_path: str,
    evidence_dir: str | None = None,
    site_name: str | None = None,
    storage_state_path: str | None = None,
    session_name: str = "action-discovery",
    headed: bool = True,
    persistent: bool = True,
    max_routes: int | None = None,
    max_actions_per_route: int = 12,
    max_safe_clicks_per_route: int = 0,
    close_on_finish: bool = True,
    include_high_risk: bool = False,
) -> dict[str, Any]:
    """Open canonical routes and write browser-observed action evidence."""

    worklist_file = resolve_workspace_path(worklist_path)
    worklist = json.loads(worklist_file.read_text(encoding="utf-8"))
    routes = worklist.get("routes", [])
    if not isinstance(routes, list):
        raise ValueError("worklist routes must be a JSON array.")

    destination = resolve_workspace_path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    evidence_root = resolve_workspace_path(
        evidence_dir or str(destination.parent / "action_discovery")
    )
    evidence_root.mkdir(parents=True, exist_ok=True)

    inferred_site_name = _slug(site_name or worklist.get("site_name") or "webapp")
    start_url = str(worklist.get("start_url") or worklist.get("base_origin") or "")
    if not start_url and routes:
        start_url = str(routes[0].get("selected_url") or "")

    intents: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    evidence_files: list[str] = []
    errors: list[dict[str, Any]] = []
    seen_intent_ids: set[str] = set()
    seen_dedupe_keys: set[str] = set()
    seen_global_control_keys: set[str] = set()

    open_result = _ADAPTER.open_browser(
        base_url=start_url,
        session_name=session_name,
        headed=headed,
        persistent=persistent,
    )
    if not open_result.ok:
        payload = {
            "schema_version": "1.0",
            "generated_at": _utc_now(),
            "worklist_path": str(worklist_file),
            "site_name": inferred_site_name,
            "summary": {
                "route_count": 0,
                "intent_count": 0,
                "skipped_count": 0,
                "error_count": 1,
            },
            "intents": [],
            "skipped_candidates": [],
            "evidence_files": [],
            "errors": [
                {
                    "reason": "open_browser_failed",
                    "message": open_result.stderr or open_result.stdout,
                }
            ],
        }
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {
            "ok": False,
            "reason": "open_browser_failed",
            "message": open_result.stderr or open_result.stdout,
            "output_path": str(destination),
        }

    if storage_state_path:
        state_path = resolve_workspace_path(storage_state_path)
        state_result = _ADAPTER.load_storage_state(session_name=session_name, path=str(state_path))
        if not state_result.ok:
            payload = {
                "schema_version": "1.0",
                "generated_at": _utc_now(),
                "worklist_path": str(worklist_file),
                "site_name": inferred_site_name,
                "summary": {
                    "route_count": 0,
                    "intent_count": 0,
                    "skipped_count": 0,
                    "error_count": 1,
                },
                "intents": [],
                "skipped_candidates": [],
                "evidence_files": [],
                "errors": [
                    {
                        "reason": "state_load_failed",
                        "path": str(state_path),
                        "message": state_result.stderr or state_result.stdout,
                    }
                ],
            }
            destination.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if close_on_finish:
                _ADAPTER.close_browser(session_name=session_name)
            return {
                "ok": False,
                "reason": "state_load_failed",
                "message": state_result.stderr or state_result.stdout,
                "output_path": str(destination),
            }

    processed_routes = 0
    try:
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
            route_evidence, route_intents, route_skipped, route_errors = _discover_one_worklist_route(
                route=route,
                site_name=inferred_site_name,
                session_name=session_name,
                evidence_root=evidence_root,
                max_actions_per_route=max_actions_per_route,
                max_safe_clicks_per_route=max_safe_clicks_per_route,
                include_high_risk=include_high_risk,
                seen_intent_ids=seen_intent_ids,
                seen_dedupe_keys=seen_dedupe_keys,
                seen_global_control_keys=seen_global_control_keys,
            )
            evidence_files.append(route_evidence)
            intents.extend(route_intents)
            skipped.extend(route_skipped)
            errors.extend(route_errors)
    finally:
        if close_on_finish:
            _ADAPTER.close_browser(session_name=session_name)

    by_type = Counter(intent["intent_type"] for intent in intents)
    by_safety = Counter(intent["safety_level"] for intent in intents)
    payload = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "worklist_path": str(worklist_file),
        "site_name": inferred_site_name,
        "summary": {
            "route_count": processed_routes,
            "intent_count": len(intents),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "by_type": dict(sorted(by_type.items())),
            "by_safety_level": dict(sorted(by_safety.items())),
        },
        "intents": intents,
        "skipped_candidates": skipped,
        "evidence_files": evidence_files,
        "errors": errors,
    }
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": not errors,
        "worklist_path": str(worklist_file),
        "output_path": str(destination),
        "evidence_dir": str(evidence_root),
        "site_name": inferred_site_name,
        "route_count": processed_routes,
        "intent_count": len(intents),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "by_type": payload["summary"]["by_type"],
        "by_safety_level": payload["summary"]["by_safety_level"],
    }


def prepare_action_intent_review_packets(
    intents_path: str,
    output_dir: str,
    max_intents_per_packet: int = 8,
    max_controls_per_packet: int = 30,
    max_forms_per_packet: int = 20,
    include_skipped_candidates: bool = True,
    clear_existing: bool = False,
) -> dict[str, Any]:
    """Prepare small route-scoped evidence packets for LLM action-intent review."""

    intents_file = resolve_workspace_path(intents_path)
    destination_dir = resolve_workspace_path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    if clear_existing:
        for old_packet in destination_dir.glob("packet_*.json"):
            if old_packet.is_file():
                old_packet.unlink()

    payload = json.loads(intents_file.read_text(encoding="utf-8"))
    intents = payload.get("intents", [])
    if not isinstance(intents, list):
        raise ValueError("intents must be a JSON array.")

    skipped_candidates = payload.get("skipped_candidates", [])
    if not isinstance(skipped_candidates, list):
        skipped_candidates = []

    site_name = str(payload.get("site_name") or "webapp")
    grouped = _group_intents_by_route(intents)
    skipped_by_route = _group_skipped_by_route(skipped_candidates)
    packets: list[dict[str, Any]] = []

    for route_id, route_intents in grouped.items():
        if not route_intents:
            continue
        evidence = _load_evidence_for_intent(route_intents[0])
        route_path = str(route_intents[0].get("entry_path") or "")
        route_skipped = skipped_by_route.get(route_path, []) if include_skipped_candidates else []
        packet_payload = {
            "schema_version": "1.0",
            "generated_at": _utc_now(),
            "source_intents_path": str(intents_file),
            "site_name": site_name,
            "review_contract": {
                "role": "LLM action-intent reviewer",
                "goal": "Accept, reject, rename, or reclassify only evidence-backed action intents for a generic SUT.",
                "rules": [
                    "Do not invent routes, controls, fields, or assertions not present in this packet.",
                    "Prefer visible labels/headings/forms over URL tokens when naming tasks.",
                    "Reject low-value controls such as numeric-only labels, raw path labels, and generic personal links.",
                    "Keep create/edit workflows conservative unless explicit submit/save policy is provided.",
                    "Preserve source.intent_id for every reviewed decision.",
                ],
            },
            "route": _review_route_summary(evidence, route_intents[0]),
            "baseline": evidence.get("baseline", {}) if isinstance(evidence, dict) else {},
            "observed_forms": _limited_list(
                evidence.get("forms", []) if isinstance(evidence, dict) else [],
                max_forms_per_packet,
            ),
            "observed_controls": _limited_list(
                evidence.get("observed_controls", []) if isinstance(evidence, dict) else [],
                max_controls_per_packet,
            ),
            "candidate_intents": [_review_intent_summary(intent) for intent in route_intents[:max_intents_per_packet]],
            "skipped_candidates": [_review_skipped_summary(item) for item in route_skipped[:max_intents_per_packet]],
            "expected_review_output_shape": {
                "reviewed_intents": [
                    {
                        "intent_id": "existing intent_id",
                        "decision": "accept | reject",
                        "intent_type": "optional corrected intent type",
                        "label": "optional improved visible label",
                        "entity": "optional corrected entity",
                        "workflow_steps": ["optional executable task steps grounded in observed fields and controls"],
                        "test_data": {"optional field label": "optional test value"},
                        "commit_policy": "optional policy, for example submit allowed for reversible create/edit workflows",
                        "success_evidence": ["optional revised assertions"],
                        "review_notes": "short reason grounded in packet evidence",
                    }
                ]
            },
        }
        packet_path = destination_dir / f"packet_{_slug(route_id)}.json"
        packet_path.write_text(
            json.dumps(packet_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        packets.append(
            {
                "path": str(packet_path),
                "route_id": route_id,
                "route": route_path,
                "candidate_count": len(packet_payload["candidate_intents"]),
                "control_count": len(packet_payload["observed_controls"]),
                "form_count": len(packet_payload["observed_forms"]),
            }
        )

    index_payload = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "source_intents_path": str(intents_file),
        "site_name": site_name,
        "packet_count": len(packets),
        "packets": packets,
    }
    index_path = destination_dir / "review_index.json"
    index_path.write_text(json.dumps(index_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "intents_path": str(intents_file),
        "output_dir": str(destination_dir),
        "index_path": str(index_path),
        "packet_count": len(packets),
        "candidate_count": len(intents),
    }


def write_reviewed_action_intents(
    source_intents_path: str,
    reviewed_intents_json: str,
    output_path: str,
    reviewer_name: str = "llm_action_reviewer",
) -> dict[str, Any]:
    """Write reviewed action intents from LLM decisions constrained to existing intents."""

    source_file = resolve_workspace_path(source_intents_path)
    destination = resolve_workspace_path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    source_payload = json.loads(source_file.read_text(encoding="utf-8"))
    source_intents = source_payload.get("intents", [])
    if not isinstance(source_intents, list):
        raise ValueError("source intents must be a JSON array.")
    source_by_id = {
        str(intent.get("intent_id") or ""): intent
        for intent in source_intents
        if isinstance(intent, dict) and intent.get("intent_id")
    }

    review_items = _decode_review_items(reviewed_intents_json)
    reviewed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for item in review_items:
        if not isinstance(item, dict):
            skipped.append({"reason": "review_item_not_object"})
            continue
        intent_id = str(item.get("intent_id") or "")
        if not intent_id or intent_id not in source_by_id:
            skipped.append({"reason": "unknown_intent_id", "intent_id": intent_id})
            continue
        if intent_id in seen_ids:
            skipped.append({"reason": "duplicate_review_decision", "intent_id": intent_id})
            continue
        seen_ids.add(intent_id)
        decision = str(item.get("decision") or "accept").strip().lower()
        if decision == "reject":
            skipped.append(
                {
                    "reason": "review_rejected",
                    "intent_id": intent_id,
                    "label": str(source_by_id[intent_id].get("label") or ""),
                    "review_notes": str(item.get("review_notes") or ""),
                }
            )
            continue
        if decision not in {"accept", "keep", "update"}:
            skipped.append({"reason": "unsupported_review_decision", "intent_id": intent_id, "decision": decision})
            continue

        reviewed_intent = _merge_review_decision(source_by_id[intent_id], item)
        reviewed.append(reviewed_intent)

    for intent_id, intent in source_by_id.items():
        if intent_id not in seen_ids:
            skipped.append(
                {
                    "reason": "not_reviewed",
                    "intent_id": intent_id,
                    "label": str(intent.get("label") or ""),
                }
            )

    payload = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "source_intents_path": str(source_file),
        "worklist_path": source_payload.get("worklist_path"),
        "site_name": source_payload.get("site_name"),
        "reviewer": reviewer_name,
        "summary": {
            "source_intent_count": len(source_intents),
            "reviewed_intent_count": len(reviewed),
            "skipped_count": len(skipped),
            "by_type": dict(sorted(Counter(intent["intent_type"] for intent in reviewed).items())),
            "by_safety_level": dict(sorted(Counter(intent["safety_level"] for intent in reviewed).items())),
        },
        "intents": reviewed,
        "skipped_candidates": list(source_payload.get("skipped_candidates", [])) + skipped,
        "review_skips": skipped,
        "evidence_files": source_payload.get("evidence_files", []),
        "errors": source_payload.get("errors", []),
    }
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "source_intents_path": str(source_file),
        "output_path": str(destination),
        "reviewed_intent_count": len(reviewed),
        "skipped_count": len(skipped),
        "by_type": payload["summary"]["by_type"],
    }


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
    intent_type = _refine_intent_type(
        intent_type=intent_type,
        text=normalized,
        label=label,
        route=route,
        source=source,
        fields=fields,
    )
    if intent_type is None and safety_level == "high_risk":
        intent_type = "high_risk_action"
    if source == "route" and intent_type == "open":
        return None
    if source == "route" and intent_type in {"create", "edit"}:
        if not _route_supports_mutation_intent(route, intent_type):
            return None
    if intent_type is None:
        return None
    if safety_level != "high_risk":
        safety_level = "read_only" if intent_type in {"search", "filter", "open"} else "safe_with_confirmation"

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


def _refine_intent_type(
    *,
    intent_type: str | None,
    text: str,
    label: str,
    route: dict[str, Any],
    source: str,
    fields: list[str],
) -> str | None:
    path = str(route.get("path") or "").lower()
    normalized_label = _normalize_text(label)
    words = set(re.findall(r"[a-z0-9]+", text))
    field_words = set(re.findall(r"[a-z0-9]+", " ".join(fields).lower()))

    if source == "route":
        return intent_type

    if {"save", "submit"} & words:
        return "edit"

    if normalized_label in {"update results", "refresh results", "apply filter"}:
        return "filter"

    if path.startswith("/reports") and (
        "results" in words or field_words & _FILTER_KEYWORDS or words & _FILTER_KEYWORDS
    ):
        return "filter"

    if intent_type == "edit" and source == "form" and field_words & _FILTER_KEYWORDS:
        return "filter"

    return intent_type


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
        str(
            form.get("label")
            or form.get("placeholder")
            or form.get("aria_label")
            or form.get("name")
            or form.get("type")
            or "Form"
        )
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


def _group_intents_by_route(intents: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for intent in intents:
        if not isinstance(intent, dict):
            continue
        route_id = str(intent.get("route_id") or intent.get("entry_path") or "route")
        grouped.setdefault(route_id, []).append(intent)
    return grouped


def _group_skipped_by_route(skipped: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in skipped:
        if not isinstance(item, dict):
            continue
        route = str(item.get("route") or "")
        grouped.setdefault(route, []).append(item)
    return grouped


def _load_evidence_for_intent(intent: dict[str, Any]) -> dict[str, Any]:
    source = intent.get("source", {}) if isinstance(intent.get("source"), dict) else {}
    evidence_path = str(source.get("evidence_file") or "")
    if not evidence_path:
        return {}
    try:
        evidence_file = resolve_workspace_path(evidence_path)
    except ValueError:
        return {}
    if not evidence_file.exists():
        return {}
    decoded = json.loads(evidence_file.read_text(encoding="utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def _review_route_summary(evidence: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    route = evidence.get("route", {}) if isinstance(evidence.get("route"), dict) else {}
    return {
        "route_id": str(intent.get("route_id") or route.get("route_id") or ""),
        "canonical_path": str(route.get("canonical_path") or intent.get("entry_path") or "/"),
        "label": str(route.get("label") or intent.get("label") or ""),
        "page_type": str(route.get("page_type") or ""),
        "phase": str(route.get("phase") or intent.get("phase") or ""),
        "require_login": bool(route.get("require_login") or intent.get("require_login")),
        "navigation_steps": route.get("navigation_steps", []) if isinstance(route.get("navigation_steps"), list) else [],
        "folded_variants": route.get("folded_variants", []) if isinstance(route.get("folded_variants"), list) else [],
    }


def _review_intent_summary(intent: dict[str, Any]) -> dict[str, Any]:
    source = intent.get("source", {}) if isinstance(intent.get("source"), dict) else {}
    return {
        "intent_id": str(intent.get("intent_id") or ""),
        "intent_type": str(intent.get("intent_type") or ""),
        "label": str(intent.get("label") or ""),
        "entity": str(intent.get("entity") or ""),
        "entry_path": str(intent.get("entry_path") or ""),
        "input_fields": intent.get("input_fields", []) if isinstance(intent.get("input_fields"), list) else [],
        "success_evidence": intent.get("success_evidence", []) if isinstance(intent.get("success_evidence"), list) else [],
        "safety_level": str(intent.get("safety_level") or ""),
        "confidence": intent.get("confidence"),
        "source": {
            "kind": str(source.get("kind") or ""),
            "evidence": str(source.get("evidence") or ""),
            "selector": str(source.get("selector") or ""),
            "control_path": str(source.get("control_path") or ""),
            "control_kind": str(source.get("control_kind") or ""),
        },
    }


def _review_skipped_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "reason": str(item.get("reason") or ""),
        "intent_type": str(item.get("intent_type") or ""),
        "label": str(item.get("label") or ""),
        "safety_level": str(item.get("safety_level") or ""),
        "confidence": item.get("confidence"),
    }


def _limited_list(values: Any, limit: int) -> list[Any]:
    if not isinstance(values, list):
        return []
    return values[: max(limit, 0)]


def _decode_review_items(reviewed_intents_json: str) -> list[Any]:
    decoded = json.loads(reviewed_intents_json)
    if isinstance(decoded, dict):
        items = decoded.get("reviewed_intents", decoded.get("intents", []))
    else:
        items = decoded
    if not isinstance(items, list):
        raise ValueError("reviewed_intents_json must be a JSON array or an object with reviewed_intents.")
    return items


def _merge_review_decision(
    source_intent: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    merged = json.loads(json.dumps(source_intent))
    for key in ("intent_type", "label", "entity", "submit_control", "safety_level"):
        value = review.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    if isinstance(review.get("input_fields"), list):
        merged["input_fields"] = [str(value).strip() for value in review["input_fields"] if str(value).strip()]
    if isinstance(review.get("success_evidence"), list):
        merged["success_evidence"] = [
            str(value).strip() for value in review["success_evidence"] if str(value).strip()
        ]
    workflow_steps = [str(value).strip() for value in review.get("workflow_steps", []) if str(value).strip()] if isinstance(review.get("workflow_steps"), list) else []
    reviewed_success = [str(value).strip() for value in review.get("success_evidence", []) if str(value).strip()] if isinstance(review.get("success_evidence"), list) else []
    confidence = review.get("confidence")
    if isinstance(confidence, int | float):
        merged["confidence"] = round(min(max(float(confidence), 0.0), 0.99), 2)
    merged["review"] = {
        "decision": str(review.get("decision") or "accept"),
        "review_notes": str(review.get("review_notes") or ""),
    }
    if workflow_steps:
        merged["review"]["workflow_steps"] = workflow_steps
    if reviewed_success:
        merged["review"]["success_evidence"] = reviewed_success
    if isinstance(review.get("test_data"), dict):
        merged["review"]["test_data"] = {
            str(key): str(value) for key, value in review["test_data"].items()
        }
    if isinstance(review.get("commit_policy"), str) and review["commit_policy"].strip():
        merged["review"]["commit_policy"] = review["commit_policy"].strip()
    return merged


def _discover_one_worklist_route(
    *,
    route: dict[str, Any],
    site_name: str,
    session_name: str,
    evidence_root: Path,
    max_actions_per_route: int,
    max_safe_clicks_per_route: int,
    include_high_risk: bool,
    seen_intent_ids: set[str],
    seen_dedupe_keys: set[str],
    seen_global_control_keys: set[str],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    canonical_path = str(route.get("canonical_path") or "/")
    route_id = str(route.get("route_id") or _slug(canonical_path))
    selected_url = str(route.get("selected_url") or canonical_path)
    evidence_file = evidence_root / f"route_{_slug(route_id or canonical_path)}.json"
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    intents: list[dict[str, Any]] = []

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
        _write_route_evidence(
            evidence_file,
            route=route,
            baseline={},
            observed_controls=[],
            forms=[],
            tables=[],
            intent_drafts=[],
            blocked_actions=[],
            safe_clicks=[],
            errors=errors,
        )
        return str(evidence_file), intents, skipped, errors

    snapshot_result = _ADAPTER.snapshot(session_name=session_name, depth=3)
    page_data, page_error = _collect_action_page_data(session_name)
    if page_error:
        errors.append(
            {
                "route_id": route_id,
                "route": canonical_path,
                "reason": "page_data_failed",
                "message": page_error,
            }
        )

    forms = _clean_live_forms(page_data.get("forms", []))
    observed_controls = _observed_controls(page_data.get("controls", []))
    if route.get("require_login") and _page_looks_like_auth_redirect(page_data):
        skipped.append(
            {
                "reason": "auth_redirect_detected",
                "route": canonical_path,
                "route_id": route_id,
                "url": page_data.get("url") or goto_result.url or selected_url,
                "title": page_data.get("title") or goto_result.title or "",
            }
        )
        _write_route_evidence(
            evidence_file,
            route=route,
            baseline={
                "url": page_data.get("url") or goto_result.url or selected_url,
                "title": page_data.get("title") or goto_result.title or "",
                "headings": _clean_strings(page_data.get("headings", [])),
                "snapshot_path": snapshot_result.snapshot_path,
                "snapshot_ok": snapshot_result.ok,
            },
            observed_controls=observed_controls,
            forms=forms,
            tables=page_data.get("tables", []) if isinstance(page_data.get("tables"), list) else [],
            intent_drafts=[],
            blocked_actions=[],
            safe_clicks=[],
            errors=[],
        )
        return str(evidence_file), intents, skipped, errors

    blocked_actions = [
        {
            "label": control.get("label"),
            "kind": control.get("kind"),
            "reason": control.get("reason") or "unsafe_action_blocked",
        }
        for control in observed_controls
        if not control.get("safe")
    ]
    candidates = _intent_candidates_from_page_evidence(
        route=route,
        page_data=page_data,
        forms=forms,
        observed_controls=observed_controls,
        site_name=site_name,
        evidence_file=str(evidence_file),
    )

    for candidate in candidates:
        if len(intents) >= max_actions_per_route:
            skipped.append(_skip_candidate(candidate, "max_actions_per_route_reached"))
            continue
        if not include_high_risk and candidate["safety_level"] == "high_risk":
            skipped.append(_skip_candidate(candidate, "high_risk"))
            continue
        dedupe_key = _dedupe_key(candidate)
        if dedupe_key in seen_dedupe_keys:
            skipped.append(_skip_candidate(candidate, "duplicate"))
            continue
        seen_dedupe_keys.add(dedupe_key)
        global_control_key = _browser_control_global_key(candidate)
        if global_control_key:
            if global_control_key in seen_global_control_keys:
                skipped.append(_skip_candidate(candidate, "global_duplicate"))
                continue
            seen_global_control_keys.add(global_control_key)
        covered_reason = _browser_control_covered_reason(candidate, intents)
        if covered_reason:
            skipped.append(_skip_candidate(candidate, covered_reason))
            continue
        candidate["intent_id"] = _unique_intent_id(candidate["intent_id"], seen_intent_ids)
        intents.append(candidate)

    intent_drafts = [
        {
            "intent_id": intent["intent_id"],
            "intent_type": intent["intent_type"],
            "label": intent["label"],
            "safety_level": intent["safety_level"],
            "confidence": intent["confidence"],
        }
        for intent in intents
    ]
    _write_route_evidence(
        evidence_file,
        route=route,
        baseline={
            "url": page_data.get("url") or goto_result.url or selected_url,
            "title": page_data.get("title") or goto_result.title or "",
            "headings": _clean_strings(page_data.get("headings", [])),
            "snapshot_path": snapshot_result.snapshot_path,
            "snapshot_ok": snapshot_result.ok,
        },
        observed_controls=observed_controls,
        forms=forms,
        tables=page_data.get("tables", []) if isinstance(page_data.get("tables"), list) else [],
        intent_drafts=intent_drafts,
        blocked_actions=blocked_actions,
        safe_clicks=[
            {
                "attempted": 0,
                "max_safe_clicks_per_route": max_safe_clicks_per_route,
                "note": "safe click exploration is reserved for a later implementation slice",
            }
        ],
        errors=errors,
    )
    return str(evidence_file), intents, skipped, errors


def _collect_action_page_data(session_name: str) -> tuple[dict[str, Any], str | None]:
    result = _ADAPTER.eval_js(session_name=session_name, script=_ACTION_PAGE_DATA_SCRIPT, raw=True)
    if not result.ok:
        return {}, result.stderr or result.stdout or "playwright-cli eval failed"
    raw = result.raw_value
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}, "page data JSON decode failed"
        if isinstance(decoded, dict):
            return decoded, None
    return {}, "page data was not an object"


def _page_looks_like_auth_redirect(page_data: dict[str, Any]) -> bool:
    url = str(page_data.get("url") or "").lower()
    title = str(page_data.get("title") or "").lower()
    headings = " ".join(_clean_strings(page_data.get("headings", []))).lower()
    forms = _clean_live_forms(page_data.get("forms", []))
    field_names = {str(form.get("name") or "").lower() for form in forms}
    has_password = any(str(form.get("type") or "").lower() == "password" for form in forms)
    has_username = bool(field_names & {"username", "email", "user", "login"})
    return (
        "/login" in url
        or "login" in headings
        or "sign in" in headings
        or (has_password and has_username)
        or ("login" in title and has_password)
    )


def _intent_candidates_from_page_evidence(
    *,
    route: dict[str, Any],
    page_data: dict[str, Any],
    forms: list[dict[str, str]],
    observed_controls: list[dict[str, Any]],
    site_name: str,
    evidence_file: str,
) -> list[dict[str, Any]]:
    canonical_path = str(route.get("canonical_path") or "/")
    route_like = {
        "id": str(route.get("route_id") or _slug(canonical_path)),
        "label": str(route.get("label") or canonical_path),
        "path": canonical_path,
        "page_type": str(route.get("page_type") or ""),
        "phase": str(route.get("phase") or ""),
        "require_login": bool(route.get("require_login")),
    }
    headings = _clean_strings(page_data.get("headings", []))
    fields = _form_field_names(forms)
    candidates: list[dict[str, Any]] = []

    page_candidate = _candidate_from_text(
        text=" ".join([str(route_like["label"]), canonical_path, str(route_like["page_type"]), " ".join(headings)]),
        label=str(route_like["label"]),
        route=route_like,
        site_name=site_name,
        route_id=str(route_like["id"]),
        entry_path=canonical_path,
        fields=fields,
        source="route",
    )
    if page_candidate:
        page_candidate["source"]["evidence_file"] = evidence_file
        page_candidate["source"]["kind"] = "browser_route"
        candidates.append(page_candidate)

    for form in forms:
        form_text = " ".join(str(value) for value in form.values() if value)
        form_candidate = _candidate_from_text(
            text=form_text,
            label=_form_label(form),
            route=route_like,
            site_name=site_name,
            route_id=str(route_like["id"]),
            entry_path=canonical_path,
            fields=_form_field_names([form]),
            source="form",
        )
        if form_candidate:
            form_candidate["source"]["evidence_file"] = evidence_file
            form_candidate["source"]["kind"] = "browser_form"
            candidates.append(form_candidate)

    for control in observed_controls:
        label = str(control.get("label") or "").strip()
        if not label or label.lower() in _LOW_VALUE_ACTIONS:
            continue
        control_candidate = _candidate_from_text(
            text=" ".join([label, str(control.get("path") or ""), str(control.get("kind") or "")]),
            label=label,
            route=route_like,
            site_name=site_name,
            route_id=str(route_like["id"]),
            entry_path=canonical_path,
            fields=[],
            source="primary_action",
        )
        if control_candidate:
            control_candidate["source"]["evidence_file"] = evidence_file
            control_candidate["source"]["kind"] = "browser_control"
            control_candidate["source"]["selector"] = control.get("selector")
            control_candidate["source"]["control_kind"] = control.get("kind")
            control_candidate["source"]["control_path"] = control.get("path")
            control_candidate["source"]["same_origin"] = control.get("same_origin")
            candidates.append(control_candidate)

    return candidates


def _observed_controls(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    controls: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        label = _clean_text(str(value.get("label") or value.get("path") or ""))
        if not label:
            continue
        control_text = " ".join(
            str(value.get(key) or "") for key in ("label", "path", "query", "href", "type", "role")
        ).lower()
        intent_type = _intent_type(control_text)
        unsafe = bool(value.get("disabled")) or is_session_ending_ui_label(control_text) or any(
            keyword in control_text for keyword in _HIGH_RISK_KEYWORDS
        )
        kind = intent_type or ("link" if value.get("href") else "control")
        controls.append(
            {
                "label": label,
                "kind": kind,
                "safe": not unsafe,
                "reason": "disabled_or_high_risk" if unsafe else "",
                "selector": str(value.get("selector") or ""),
                "tag": str(value.get("tag") or ""),
                "path": str(value.get("path") or ""),
                "same_origin": bool(value.get("same_origin", True)),
            }
        )
    return controls[:80]


def _clean_live_forms(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    forms: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, dict) or value.get("disabled"):
            continue
        form = {
            str(key): str(raw).strip()
            for key, raw in value.items()
            if raw not in (None, "") and key not in {"disabled"}
        }
        if form:
            forms.append(form)
    return forms[:80]


def _write_route_evidence(
    path: Path,
    *,
    route: dict[str, Any],
    baseline: dict[str, Any],
    observed_controls: list[dict[str, Any]],
    forms: list[dict[str, str]],
    tables: list[Any],
    intent_drafts: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    safe_clicks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    payload = {
        "schema_version": "1.0",
        "generated_at": _utc_now(),
        "route": route,
        "baseline": baseline,
        "observed_controls": observed_controls,
        "forms": forms,
        "tables": tables,
        "intent_drafts": intent_drafts,
        "blocked_actions": blocked_actions,
        "safe_clicks": safe_clicks,
        "errors": errors,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _dedupe_key(candidate: dict[str, Any]) -> str:
    source = candidate.get("source", {}) if isinstance(candidate.get("source"), dict) else {}
    source_kind = str(source.get("kind") or "")
    if source_kind == "browser_control":
        return "|".join(
            [
                "browser_control",
                str(candidate.get("route_id") or ""),
                str(candidate.get("intent_type") or ""),
                _normalize_text(str(candidate.get("label") or "")),
                _normalize_text(str(source.get("selector") or "")),
                _normalize_text(str(source.get("control_path") or "")),
            ]
        )
    if source_kind.startswith("browser_"):
        return "|".join(
            [
                "browser",
                str(candidate.get("route_id") or ""),
                str(candidate.get("intent_type") or ""),
                str(candidate.get("entity") or ""),
            ]
        )
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


def _browser_control_global_key(candidate: dict[str, Any]) -> str | None:
    source = candidate.get("source", {}) if isinstance(candidate.get("source"), dict) else {}
    if source.get("kind") != "browser_control":
        return None
    label = _normalize_text(str(candidate.get("label") or ""))
    selector = _normalize_text(str(source.get("selector") or ""))
    control_path = _normalize_text(str(source.get("control_path") or ""))
    if not label and not selector and not control_path:
        return None
    return "|".join(
        [
            "browser_control_global",
            str(candidate.get("intent_type") or ""),
            label,
            selector,
            control_path,
        ]
    )


def _browser_control_covered_reason(
    candidate: dict[str, Any],
    accepted_intents: list[dict[str, Any]],
) -> str | None:
    source = candidate.get("source", {}) if isinstance(candidate.get("source"), dict) else {}
    if source.get("kind") != "browser_control":
        return None

    route_id = str(candidate.get("route_id") or "")
    intent_type = str(candidate.get("intent_type") or "")
    for intent in accepted_intents:
        if str(intent.get("route_id") or "") != route_id:
            continue
        if str(intent.get("intent_type") or "") != intent_type:
            continue
        accepted_source = (
            intent.get("source", {}) if isinstance(intent.get("source"), dict) else {}
        )
        accepted_kind = str(accepted_source.get("kind") or "")
        if accepted_kind == "browser_form" and intent_type in {"search", "filter"}:
            return "covered_by_form_intent"
        if accepted_kind == "browser_route" and intent_type in {"create", "edit"}:
            return "covered_by_route_intent"
    return None


def _route_key(route: dict[str, Any]) -> str:
    path = str(route.get("path") or "/")
    query = str(route.get("query") or "")
    return f"{path}?{query}" if query else path


def _normalize_route_key(route_key: str) -> str:
    parsed = urlsplit(route_key or "/")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(("", "", path, parsed.query, ""))


def _canonical_action_route_key(route_key: str, *, skip_query_variants: bool) -> str:
    parsed = urlsplit(route_key or "/")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = "" if skip_query_variants else parsed.query
    return urlunsplit(("", "", path, query, ""))


def _action_route_skip_reason(
    *,
    route: dict[str, Any],
    route_key: str,
    canonical_key: str,
    include_patterns: list[str],
    exclude_patterns: list[str],
    include_unsafe_routes: bool,
) -> str | None:
    if _route_has_invalid_query(route_key):
        return "invalid_query"
    if include_patterns and not (
        _matches_any(route_key, include_patterns) or _matches_any(canonical_key, include_patterns)
    ):
        return "include_pattern_mismatch"
    if exclude_patterns and (
        _matches_any(route_key, exclude_patterns) or _matches_any(canonical_key, exclude_patterns)
    ):
        return "exclude_pattern_match"
    if not include_unsafe_routes and _route_looks_unsafe_for_action_discovery(route):
        return "unsafe_route"
    return None


def _route_looks_unsafe_for_action_discovery(route: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(route.get(key) or "") for key in ("label", "path", "query", "url", "page_type")
    ).lower()
    return is_session_ending_ui_label(haystack) or any(
        keyword in haystack for keyword in _HIGH_RISK_KEYWORDS
    )


def _select_canonical_route(routes: list[dict[str, Any]], canonical_key: str) -> dict[str, Any]:
    for route in routes:
        if _normalize_route_key(_route_key(route)) == canonical_key:
            return route
    return routes[0]


def _canonical_route_url(base_origin: str, canonical_key: str, selected: dict[str, Any]) -> str:
    if base_origin:
        return urljoin(f"{base_origin}/", canonical_key.lstrip("/"))
    selected_url = str(selected.get("url") or "")
    if selected_url:
        parsed = urlsplit(selected_url)
        canonical = urlsplit(canonical_key)
        return urlunsplit((parsed.scheme, parsed.netloc, canonical.path or "/", canonical.query, ""))
    return canonical_key


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


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


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
