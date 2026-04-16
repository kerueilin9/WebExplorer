"""Thin subprocess adapter around playwright-cli."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from adk_playwright_agent.app.models import CommandResult

_PAGE_URL_RE = re.compile(r"^- Page URL: (?P<value>.+)$", re.MULTILINE)
_PAGE_TITLE_RE = re.compile(r"^- Page Title: (?P<value>.+)$", re.MULTILINE)
_SNAPSHOT_RE = re.compile(r"\[Snapshot\]\((?P<value>[^)]+)\)")


class PlaywrightCliAdapter:
    """Execute structured playwright-cli commands."""

    def __init__(self, cli_bin: str | None = None, cwd: Path | None = None) -> None:
        requested_cli = cli_bin or os.getenv("PLAYWRIGHT_CLI_BIN", "playwright-cli")
        self.command_prefix = self._resolve_cli_command(requested_cli)
        self.cli_bin = self.command_prefix[0]
        workspace_root = os.getenv("AGENT_WORKSPACE_ROOT")
        self.cwd = cwd or Path(workspace_root or Path.cwd()).resolve()

    def open_browser(
        self,
        base_url: str,
        session_name: str,
        headed: bool,
        persistent: bool,
    ) -> CommandResult:
        args = self._session_args(session_name) + ["open", base_url]
        if headed:
            args.append("--headed")
        if persistent:
            args.append("--persistent")
        return self._run(args)

    def goto(self, session_name: str, url: str) -> CommandResult:
        return self._run(self._session_args(session_name) + ["goto", url])

    def snapshot(self, session_name: str, depth: int | None = None) -> CommandResult:
        args = self._session_args(session_name) + ["snapshot"]
        if depth is not None:
            args.extend(["--depth", str(depth)])
        return self._run(args)

    def click(self, session_name: str, target: str) -> CommandResult:
        return self._run(self._session_args(session_name) + ["click", target])

    def fill(self, session_name: str, target: str, text: str, submit: bool) -> CommandResult:
        args = self._session_args(session_name) + ["fill", target, text]
        if submit:
            args.append("--submit")
        return self._run(args)

    def press_key(self, session_name: str, key: str) -> CommandResult:
        return self._run(self._session_args(session_name) + ["press", key])

    def eval_js(self, session_name: str, script: str, raw: bool) -> CommandResult:
        args = self._session_args(session_name)
        if raw:
            args.append("--raw")
        args.extend(["eval", script])
        result = self._run(args)
        result.raw_value = _coerce_raw_value(result.stdout)
        return result

    def save_storage_state(self, session_name: str, path: str) -> CommandResult:
        return self._run(self._session_args(session_name) + ["state-save", path])

    def close_browser(self, session_name: str) -> CommandResult:
        return self._run(self._session_args(session_name) + ["close"])

    def _session_args(self, session_name: str) -> list[str]:
        return [f"-s={session_name}"]

    def _run(self, args: list[str], timeout_sec: int = 90) -> CommandResult:
        command = [*self.command_prefix, *args]
        completed = subprocess.run(
            command,
            cwd=self.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        url = _match_value(_PAGE_URL_RE, stdout)
        title = _match_value(_PAGE_TITLE_RE, stdout)
        snapshot_path = _match_value(_SNAPSHOT_RE, stdout)
        return CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            url=url,
            title=title,
            snapshot_path=snapshot_path,
        )

    @staticmethod
    def _resolve_cli_command(value: str) -> list[str]:
        candidate = Path(value)
        if candidate.is_absolute() and candidate.exists():
            return _expand_windows_npm_cmd(candidate)

        resolved = shutil.which(value)
        if resolved:
            return _expand_windows_npm_cmd(Path(resolved))

        for suffix in (".cmd", ".exe", ".bat", ".ps1"):
            resolved = shutil.which(f"{value}{suffix}")
            if resolved:
                return _expand_windows_npm_cmd(Path(resolved))

        raise FileNotFoundError(
            f"Could not resolve '{value}' in PATH. Set PLAYWRIGHT_CLI_BIN to an absolute path if needed."
        )


def _match_value(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    return match.group("value").strip()


def _expand_windows_npm_cmd(path: Path) -> list[str]:
    """Avoid .cmd argument parsing so URLs containing '&' stay intact on Windows."""

    if path.suffix.lower() not in {".cmd", ".bat"}:
        return [str(path)]

    script = path.parent / "node_modules" / "@playwright" / "cli" / "playwright-cli.js"
    if not script.exists():
        return [str(path)]

    node = path.parent / "node.exe"
    if not node.exists():
        resolved_node = shutil.which("node")
        node = Path(resolved_node) if resolved_node else Path("node")
    return [str(node), str(script)]


def _coerce_raw_value(stdout: str):
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip().strip('"')
