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
    max_options_per_group: int = 3,
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
        compacted_content, compacted_options_count = _compact_snapshot_options(
            normalized_content,
            max_options_per_group=max_options_per_group,
        )
        compacted["page_snapshot"] = {
            "source": snapshot.get("source") or "",
            "path": snapshot.get("path") or "",
            "content": compacted_content,
            "option_groups_compacted": compacted_options_count,
            "original_char_count": len(normalized_content),
            "compacted_char_count": len(compacted_content),
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


def _compact_snapshot_options(
    content: str,
    *,
    max_options_per_group: int,
) -> tuple[str, int]:
    if not content:
        return "", 0

    lines = content.splitlines()
    compacted_lines: list[str] = []
    group: list[str] = []
    compacted_group_count = 0

    for line in lines:
        if _is_option_line(line):
            group.append(line)
            continue
        compacted_group_count += _flush_option_group(
            group,
            compacted_lines,
            max_options_per_group=max_options_per_group,
        )
        group = []
        compacted_lines.append(line)

    compacted_group_count += _flush_option_group(
        group,
        compacted_lines,
        max_options_per_group=max_options_per_group,
    )

    return "\n".join(compacted_lines), compacted_group_count


def _flush_option_group(
    group: list[str],
    output: list[str],
    *,
    max_options_per_group: int,
) -> int:
    if not group:
        return 0
    limit = max(0, max_options_per_group)
    if len(group) <= limit:
        output.extend(group)
        return 0
    output.extend(group[:limit])
    skipped_count = len(group) - limit
    indent = group[0][: len(group[0]) - len(group[0].lstrip())]
    output.append(f"{indent}... [{skipped_count} options omitted] ...")
    return 1


def _is_option_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith("- "):
        return False
    option_text = stripped[2:].lstrip("'\"")
    return option_text.startswith("option ")


def _pick_keys(item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        if key in item:
            result[key] = item[key]
    return result
