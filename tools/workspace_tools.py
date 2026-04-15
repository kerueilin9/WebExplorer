"""Workspace file system tools."""

from __future__ import annotations

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
    return {
        "path": str(file_path),
        "content": file_path.read_text(encoding="utf-8"),
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
