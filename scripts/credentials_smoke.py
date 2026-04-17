"""Smoke tests for recoverable credentials loading errors."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from adk_playwright_agent.adapters.credentials import (
    CredentialsError,
    load_named_credentials,
)
from adk_playwright_agent.tools.browser_tools import login_from_notes
from adk_playwright_agent.tools.crawler_tools import _perform_login
from adk_playwright_agent.app.context_memory import CrawlerContext


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    missing_path = project_root / ".adk" / "missing-passwords.txt"

    try:
        load_named_credentials(str(missing_path), "missing-system")
    except CredentialsError as exc:
        loader_result = {
            "ok": False,
            "reason": "credentials_unavailable",
            "message": str(exc),
        }
    else:
        raise AssertionError("Expected CredentialsError.")

    browser_tool_result = login_from_notes(
        session_name="credentials-smoke",
        system_name="missing-system",
        username_target="input[name=username]",
        password_target="input[name=password]",
        submit_button_target="button[type=submit]",
        credentials_path=str(missing_path),
    )

    crawler_login_result = _perform_login(
        session_name="credentials-smoke",
        base_origin="http://localhost:3102",
        login_path="/login",
        credentials_system_name="missing-system",
        credentials_path=str(missing_path),
        storage_state_path=None,
        context=CrawlerContext(),
    )

    print(
        json.dumps(
            {
                "loader_result": loader_result,
                "browser_tool_result": browser_tool_result,
                "crawler_login_result": crawler_login_result,
            },
            indent=2,
        )
    )

    assert browser_tool_result["ok"] is False
    assert browser_tool_result["reason"] == "credentials_unavailable"
    assert crawler_login_result["ok"] is False
    assert crawler_login_result["reason"] == "credentials_unavailable"


if __name__ == "__main__":
    main()
