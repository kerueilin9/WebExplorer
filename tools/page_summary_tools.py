"""Vertex-backed page summary generation from page observation artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adk_playwright_agent.adapters.vertex_genai import VertexGenAIAdapter
from adk_playwright_agent.app.policies import resolve_workspace_path
from adk_playwright_agent.app.vertex_prompts import PAGE_SUMMARY_PROMPT

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard
    yaml = None

_VERTEX = VertexGenAIAdapter()


def summarize_pages_with_vertex(
    observation_index_path: str,
    summary_index_path: str,
    summary_output_dir: str | None = None,
    site_name: str | None = None,
    max_pages: int | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Generate one page summary per observation artifact using Vertex AI."""

    index_file = resolve_workspace_path(observation_index_path)
    summary_index_file = resolve_workspace_path(summary_index_path)
    if not index_file.exists() or not index_file.is_file():
        return _error("file_not_found", f"File not found: {index_file}", observation_index_path=str(index_file))

    try:
        observation_index = json.loads(index_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error("invalid_json", f"Invalid JSON in '{index_file}': {exc}", observation_index_path=str(index_file))

    observations = observation_index.get("observations", [])
    if not isinstance(observations, list):
        return _error("invalid_index_schema", "observations must be a JSON array.", observation_index_path=str(index_file))

    summary_root = resolve_workspace_path(summary_output_dir or str(summary_index_file.parent / "page_summaries"))
    summary_root.mkdir(parents=True, exist_ok=True)
    summary_index_file.parent.mkdir(parents=True, exist_ok=True)

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
        output_path = summary_root / f"{page_id}.summary.json"
        if output_path.exists() and not overwrite:
            skipped.append({"reason": "exists", "path": str(output_path), "page_id": page_id})
            generated.append({"page_id": page_id, "path": str(output_path), "source_page": str(page_path), "cached": True})
            continue

        prompt = _page_summary_prompt(inferred_site_name, page_payload)
        result = _VERTEX.generate_json(prompt=prompt, temperature=0.2, max_output_tokens=2048)
        if not result.get("ok"):
            errors.append({"reason": str(result.get("error") or "vertex_failed"), "message": str(result.get("message") or ""), "page_id": page_id, "path": str(page_path)})
            continue

        summary_payload = _normalize_page_summary(
            page_payload=page_payload,
            model_payload=result["data"],
        )
        output_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        generated.append(
            {
                "page_id": page_id,
                "route": str(summary_payload.get("route") or ""),
                "path": str(output_path),
                "source_page": str(page_path),
            }
        )

    summary_index_payload = {
        "schema_version": "1.0",
        "observation_index_path": str(index_file),
        "site_name": inferred_site_name,
        "summary_dir": str(summary_root),
        "summary_count": len(generated),
        "error_count": len(errors),
        "skipped_count": len(skipped),
        "summaries": generated,
        "errors": errors,
        "skipped": skipped,
    }
    summary_index_file.write_text(json.dumps(summary_index_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": not errors,
        "observation_index_path": str(index_file),
        "summary_index_path": str(summary_index_file),
        "summary_output_dir": str(summary_root),
        "site_name": inferred_site_name,
        "summary_count": len(generated),
        "error_count": len(errors),
        "skipped_count": len(skipped),
    }


def _page_summary_prompt(site_name: str, page_payload: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            PAGE_SUMMARY_PROMPT,
            f"SITE_NAME: {site_name}",
            "PAGE_ARTIFACT:",
            _render_page_payload(page_payload),
        ]
    )


def _normalize_page_summary(*, page_payload: dict[str, Any], model_payload: dict[str, Any]) -> dict[str, Any]:
    baseline = page_payload.get("baseline", {}) if isinstance(page_payload.get("baseline"), dict) else {}
    route = page_payload.get("route", {}) if isinstance(page_payload.get("route"), dict) else {}
    plain_language_summary = str(model_payload.get("plain_language_summary") or "").strip()
    plain_language_summary = plain_language_summary[:240]
    return {
        "schema_version": "1.0",
        "page_id": str(page_payload.get("page_id") or ""),
        "route": str(route.get("canonical_path") or route.get("path") or "/"),
        "url": str(baseline.get("url") or ""),
        "title": str(baseline.get("title") or ""),
        "navigation_steps": _string_list(route.get("navigation_steps")),
        "plain_language_summary": plain_language_summary,
        "page_purpose": str(model_payload.get("page_purpose") or "").strip(),
        "main_entities": _string_list(model_payload.get("main_entities")),
        "key_forms": _string_list(model_payload.get("key_forms")),
        "key_actions": _string_list(model_payload.get("key_actions")),
        "likely_user_goals": _string_list(model_payload.get("likely_user_goals")),
        "risk_notes": _string_list(model_payload.get("risk_notes")),
        "evidence": _string_list(model_payload.get("evidence")),
        "source_page_artifact": str(page_payload.get("_source_path") or ""),
    }


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


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message, **extra}
