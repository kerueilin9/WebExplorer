"""Helpers for reading credentials from workspace notes files."""

from __future__ import annotations

import os
import re
from pathlib import Path


class CredentialsError(ValueError):
    """Base error for user-recoverable credential loading failures."""


class CredentialsFileNotFoundError(CredentialsError):
    """Raised when no configured credentials file can be found."""


def load_named_credentials(path: str | None, system_name: str) -> dict[str, str]:
    """Load credentials from notes formatted like '<name> 帳號：...'."""

    file_path = resolve_credentials_path(path)
    content = file_path.read_text(encoding="utf-8")

    username_pattern = re.compile(
        rf"^{re.escape(system_name)}\s*帳號[:：]\s*(?P<value>.+)$",
        re.MULTILINE,
    )
    password_pattern = re.compile(
        rf"^{re.escape(system_name)}\s*密碼[:：]\s*(?P<value>.+)$",
        re.MULTILINE,
    )

    username_match = username_pattern.search(content)
    password_match = password_pattern.search(content)

    if not username_match or not password_match:
        raise CredentialsError(
            f"Could not find credentials for '{system_name}' in '{file_path}'."
        )

    return {
        "username": username_match.group("value").strip(),
        "password": password_match.group("value").strip(),
        "source": str(file_path),
    }


def resolve_credentials_path(path: str | None) -> Path:
    """Resolve a credentials file path with safe project-local fallbacks."""

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path).expanduser())

    project_root = Path(__file__).resolve().parents[1]
    workspace_root = os.getenv("AGENT_WORKSPACE_ROOT")
    if workspace_root:
        candidates.append(Path(workspace_root).expanduser() / "passwords.txt")
    candidates.extend(
        [
            project_root / "passwords.txt",
            Path.cwd() / "passwords.txt",
        ]
    )

    checked: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        checked.append(str(resolved))
        if resolved.is_file():
            return resolved

    raise CredentialsFileNotFoundError(
        "Credentials file not found. Checked: " + "; ".join(checked)
    )
