"""Shared data structures used by tools and adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CommandResult:
    """Structured result returned by command-backed adapters."""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    url: str | None = None
    title: str | None = None
    snapshot_path: str | None = None
    raw_value: Any | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_tool_result(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


@dataclass(slots=True)
class ValidationIssue:
    """A single validation issue."""

    path: str
    message: str
    severity: str = "error"


@dataclass(slots=True)
class ValidationSummary:
    """Summary of validating one or more generated files."""

    total_files: int
    valid_files: int
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "total_files": self.total_files,
            "valid_files": self.valid_files,
            "invalid_files": self.total_files - self.valid_files,
            "issues": [asdict(issue) for issue in self.issues],
        }
