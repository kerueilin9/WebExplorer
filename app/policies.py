"""Safety and workspace policy helpers."""

from __future__ import annotations

import os
from pathlib import Path

DANGEROUS_UI_KEYWORDS = {
    "delete",
    "remove",
    "disable",
    "purge",
    "reset",
    "erase",
    "restart",
    "rebuild",
}

SESSION_ENDING_UI_KEYWORDS = {
    "log out",
    "log-out",
    "logout",
    "sign out",
    "sign-out",
    "signoff",
    "sign off",
    "sign-off",
    "signout",
}


def workspace_root() -> Path:
    """Return the configured workspace root."""

    root = os.getenv("AGENT_WORKSPACE_ROOT")
    if root:
        return Path(root).resolve()
    return Path.cwd().resolve()


def resolve_workspace_path(path: str) -> Path:
    """Resolve a path and ensure it stays within the configured workspace."""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace_root() / candidate
    resolved = candidate.resolve()
    root = workspace_root()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path '{resolved}' is outside workspace root '{root}'.") from exc
    return resolved


def is_destructive_ui_label(label: str) -> bool:
    """Return true if a UI action appears destructive."""

    normalized = label.strip().lower()
    return any(keyword in normalized for keyword in DANGEROUS_UI_KEYWORDS)


def is_session_ending_ui_label(label: str) -> bool:
    """Return true if a UI action appears to end the signed-in session."""

    normalized = label.strip().lower()
    compact = normalized.replace(" ", "").replace("-", "").replace("_", "")
    return any(keyword in normalized for keyword in SESSION_ENDING_UI_KEYWORDS) or compact in {
        "logout",
        "signout",
        "signoff",
    }
