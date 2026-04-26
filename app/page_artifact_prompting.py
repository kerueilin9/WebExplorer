"""Helpers for compacting page observation artifacts before LLM prompting."""

from __future__ import annotations

from typing import Any


_FORM_KEYS = ("tag", "type", "name", "label", "placeholder", "required", "disabled", "selector")
_TABLE_KEYS = ("text",)


def compact_page_artifact_for_prompt(
    page_payload: dict[str, Any],
    *,
    max_forms: int = 20,
    max_tables: int = 10,
    snapshot_head_lines: int = 140,
    snapshot_tail_lines: int = 40,
    snapshot_char_limit: int = 12000,
) -> dict[str, Any]:
    """Return a prompt-friendly subset of a page artifact."""

    compacted: dict[str, Any] = {}
    for key in ("page_id", "route", "baseline", "errors"):
        value = page_payload.get(key)
        if value is not None:
            compacted[key] = value

    snapshot = page_payload.get("page_snapshot")
    if isinstance(snapshot, dict):
        raw_content = str(snapshot.get("content") or "")
        normalized_content = _normalize_snapshot_content(raw_content)
        compacted_content, truncated = _compact_snapshot_content(
            normalized_content,
            head_lines=snapshot_head_lines,
            tail_lines=snapshot_tail_lines,
            char_limit=snapshot_char_limit,
        )
        compacted["page_snapshot"] = {
            "source": snapshot.get("source") or "",
            "path": snapshot.get("path") or "",
            "content": compacted_content,
            "content_truncated": truncated,
            "original_char_count": len(normalized_content),
        }

    forms = page_payload.get("forms")
    if isinstance(forms, list):
        compacted["forms"] = [
            _pick_keys(item, _FORM_KEYS)
            for item in forms[:max_forms]
            if isinstance(item, dict)
        ]
        compacted["form_count"] = len(forms)
        compacted["forms_truncated"] = len(forms) > max_forms

    tables = page_payload.get("tables")
    if isinstance(tables, list):
        compacted["tables"] = [
            _pick_keys(item, _TABLE_KEYS)
            for item in tables[:max_tables]
            if isinstance(item, dict)
        ]
        compacted["table_count"] = len(tables)
        compacted["tables_truncated"] = len(tables) > max_tables

    return compacted


def _normalize_snapshot_content(content: str) -> str:
    if "\\n" in content and "\n" not in content:
        return content.replace("\\n", "\n")
    return content


def _compact_snapshot_content(
    content: str,
    *,
    head_lines: int,
    tail_lines: int,
    char_limit: int,
) -> tuple[str, bool]:
    if not content:
        return "", False

    lines = content.splitlines()
    truncated = False

    if len(lines) > head_lines + tail_lines:
        kept_lines = lines[:head_lines] + ["... [snapshot truncated] ..."] + lines[-tail_lines:]
        content = "\n".join(kept_lines)
        truncated = True

    if len(content) > char_limit:
        head_chars = max(1, (char_limit - 24) // 2)
        tail_chars = max(1, char_limit - 24 - head_chars)
        content = f"{content[:head_chars]}\n... [snapshot truncated] ...\n{content[-tail_chars:]}"
        truncated = True

    return content, truncated


def _pick_keys(item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        if key in item:
            result[key] = item[key]
    return result
