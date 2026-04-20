"""Validation helpers for generated task files."""

from __future__ import annotations

import json
from pathlib import Path

from adk_playwright_agent.app.models import ValidationIssue, ValidationSummary
from adk_playwright_agent.app.policies import resolve_workspace_path

REQUIRED_TASK_KEYS = {
    "sites",
    "task_id",
    "require_login",
    "start_url",
    "gherkin",
    "eval",
}


def validate_task_file(path: str, expected_start_url: str | None = None) -> dict:
    """Validate one generated task JSON file."""

    file_path = resolve_workspace_path(path)
    if not file_path.exists() or not file_path.is_file():
        summary = ValidationSummary(
            total_files=1,
            valid_files=0,
            issues=[
                ValidationIssue(
                    path=str(file_path),
                    message=f"File not found: {file_path}",
                )
            ],
        )
        payload = summary.to_tool_result()
        payload["ok"] = False
        payload["error"] = "file_not_found"
        payload["path"] = str(file_path)
        payload["exists"] = False
        return payload

    summary = _validate_paths([file_path], expected_start_url=expected_start_url)
    payload = summary.to_tool_result()
    payload["ok"] = bool(payload.get("invalid_files", 0) == 0)
    payload["path"] = str(file_path)
    payload["exists"] = True
    return payload


def validate_task_directory(
    directory: str,
    glob: str = "task_*.json",
    expected_start_url: str | None = None,
) -> dict:
    """Validate all task JSON files under a directory."""

    directory_path = resolve_workspace_path(directory)
    if not directory_path.exists() or not directory_path.is_dir():
        summary = ValidationSummary(
            total_files=0,
            valid_files=0,
            issues=[
                ValidationIssue(
                    path=str(directory_path),
                    message=f"Directory not found: {directory_path}",
                )
            ],
        )
        payload = summary.to_tool_result()
        payload["ok"] = False
        payload["error"] = "directory_not_found"
        payload["directory"] = str(directory_path)
        payload["glob"] = glob
        payload["exists"] = False
        return payload

    files = sorted(path for path in directory_path.glob(glob) if path.is_file())
    summary = _validate_paths(files, expected_start_url=expected_start_url)
    payload = summary.to_tool_result()
    payload["ok"] = bool(payload.get("invalid_files", 0) == 0)
    payload["directory"] = str(directory_path)
    payload["glob"] = glob
    payload["exists"] = True
    return payload


def _validate_paths(
    files: list[Path],
    expected_start_url: str | None,
) -> ValidationSummary:
    issues: list[ValidationIssue] = []
    valid_files = 0

    for file_path in files:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except OSError as exc:
            issues.append(
                ValidationIssue(
                    path=str(file_path),
                    message=f"Unable to read file: {exc}",
                )
            )
            continue
        except json.JSONDecodeError as exc:
            issues.append(
                ValidationIssue(
                    path=str(file_path),
                    message=f"Invalid JSON: {exc}",
                )
            )
            continue

        missing = sorted(REQUIRED_TASK_KEYS - payload.keys())
        if missing:
            issues.append(
                ValidationIssue(
                    path=str(file_path),
                    message=f"Missing required keys: {', '.join(missing)}",
                )
            )
            continue

        if expected_start_url and payload.get("start_url") != expected_start_url:
            issues.append(
                ValidationIssue(
                    path=str(file_path),
                    message=(
                        f"Unexpected start_url '{payload.get('start_url')}', "
                        f"expected '{expected_start_url}'."
                    ),
                )
            )
            continue

        valid_files += 1

    return ValidationSummary(
        total_files=len(files),
        valid_files=valid_files,
        issues=issues,
    )
