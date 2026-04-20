"""Workspace file system tools."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from adk_playwright_agent.app.policies import resolve_workspace_path, workspace_root


def list_files(path: str = ".", glob: str = "*") -> dict:
    """List files under the workspace root."""

    base_path = resolve_workspace_path(path)
    if base_path.is_file():
        files = [str(base_path)]
    else:
        files = sorted(str(item) for item in base_path.glob(glob))
    return {
        "workspace_root": str(workspace_root()),
        "path": str(base_path),
        "files": files,
    }


def read_text_file(path: str) -> dict:
    """Read a UTF-8 text file from the workspace."""

    file_path = resolve_workspace_path(path)
    if not file_path.exists() or not file_path.is_file():
        return {
            "ok": False,
            "path": str(file_path),
            "exists": False,
            "error": "file_not_found",
            "message": f"File not found: {file_path}",
            "content": None,
        }

    return {
        "ok": True,
        "path": str(file_path),
        "exists": True,
        "content": file_path.read_text(encoding="utf-8"),
    }


def read_json_file(path: str) -> dict[str, Any]:
    """Read a JSON file from the workspace and return parsed data."""

    file_path = resolve_workspace_path(path)
    if not file_path.exists() or not file_path.is_file():
        return {
            "ok": False,
            "path": str(file_path),
            "exists": False,
            "error": "file_not_found",
            "message": f"File not found: {file_path}",
            "data": None,
        }

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in '{file_path}': {exc}") from exc

    return {
        "ok": True,
        "path": str(file_path),
        "exists": True,
        "data": payload,
    }


def write_text_file(path: str, content: str, overwrite: bool = True) -> dict:
    """Write a UTF-8 text file inside the workspace."""

    file_path = resolve_workspace_path(path)
    existed = file_path.exists()
    if existed and not overwrite:
        raise FileExistsError(f"File already exists: {file_path}")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return {
        "path": str(file_path),
        "bytes_written": file_path.stat().st_size,
        "overwrote": existed,
    }


def write_json_file(
    path: str,
    data: Any,
    overwrite: bool = True,
    indent: int = 2,
    sort_keys: bool = False,
) -> dict[str, Any]:
    """Write JSON data inside the workspace."""

    if indent < 0:
        raise ValueError("indent must be >= 0")

    file_path = resolve_workspace_path(path)
    existed = file_path.exists()
    if existed and not overwrite:
        raise FileExistsError(f"File already exists: {file_path}")

    serialized = json.dumps(
        data,
        ensure_ascii=False,
        indent=indent,
        sort_keys=sort_keys,
    )
    if not serialized.endswith("\n"):
        serialized += "\n"

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(serialized, encoding="utf-8")
    return {
        "path": str(file_path),
        "bytes_written": file_path.stat().st_size,
        "overwrote": existed,
    }


def merge_json_files(
    base_path: str,
    override_path: str,
    output_path: str | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Deep-merge two JSON object files, with override values taking precedence."""

    base = read_json_file(base_path)
    override = read_json_file(override_path)

    if not base.get("exists", True):
        return {
            "ok": False,
            "error": "base_file_not_found",
            "base_path": base.get("path"),
            "override_path": override.get("path"),
            "message": base.get("message") or f"File not found: {base.get('path')}",
        }

    if not override.get("exists", True):
        return {
            "ok": False,
            "error": "override_file_not_found",
            "base_path": base.get("path"),
            "override_path": override.get("path"),
            "message": override.get("message") or f"File not found: {override.get('path')}",
        }

    base_data = base.get("data")
    override_data = override.get("data")

    if not isinstance(base_data, dict) or not isinstance(override_data, dict):
        raise ValueError("merge_json_files requires both inputs to be JSON objects.")

    merged = _deep_merge_json(base_data, override_data)
    result: dict[str, Any] = {
        "ok": True,
        "base_path": base["path"],
        "override_path": override["path"],
        "merged": merged,
    }

    if output_path:
        write_result = write_json_file(
            path=output_path,
            data=merged,
            overwrite=overwrite,
        )
        result.update(
            {
                "output_path": write_result["path"],
                "bytes_written": write_result["bytes_written"],
                "overwrote": write_result["overwrote"],
            }
        )

    return result


def _deep_merge_json(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_json(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
