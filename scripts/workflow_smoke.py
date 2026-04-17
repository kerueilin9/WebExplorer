"""Smoke test for the manifest-first workflow orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from adk_playwright_agent.tools import workflow_tools


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    auth_calls: list[dict[str, Any]] = []
    workflow_tools.crawl_site_to_manifest = _fake_guest_crawl
    workflow_tools.crawl_authenticated_site_to_manifest = _auth_crawl_spy(auth_calls)

    result = workflow_tools.run_manifest_first_route_workflow(
        start_url="http://localhost:3102",
        site_name="sample_workflow",
        output_root="adk_playwright_agent/.adk/workflow_smoke",
        credentials_system_name="sample_workflow",
        storage_state_path=".auth/sample_workflow_state.json",
        max_pages=10,
        guest_max_depth=1,
        auth_max_depth=1,
        headed=False,
        persistent=False,
    )

    print(json.dumps(result, indent=2))

    assert result["ok"] is True
    assert result["summary"]["guest_generated_count"] == 3
    assert result["summary"]["auth_generated_count"] == 1
    assert result["summary"]["guest_valid_files"] == 3
    assert result["summary"]["auth_valid_files"] == 1
    assert result["summary"]["login_path"] == "/signin"
    assert result["summary"]["login_path_source"] == "guest_manifest"
    assert auth_calls[0]["login_path"] == "/signin"


def _fake_guest_crawl(**kwargs: Any) -> dict[str, Any]:
    return _write_fake_manifest(
        output_path=kwargs["output_path"],
        start_url=kwargs["start_url"],
        phase="guest",
        require_login=False,
        routes=[
            _fake_route(
                start_url=kwargs["start_url"],
                phase="guest",
                require_login=False,
                path="/",
                label="Home",
                page_type="home",
            ),
            _fake_route(
                start_url=kwargs["start_url"],
                phase="guest",
                require_login=False,
                path="/forgot-password",
                label="Forgot password?",
                page_type="auth",
                context={
                    "headings": ["Forgot password"],
                    "primary_actions": ["Reset password"],
                    "forms": [
                        {"tag": "input", "type": "email", "name": "email"},
                    ],
                },
            ),
            _fake_route(
                start_url=kwargs["start_url"],
                phase="guest",
                require_login=False,
                path="/signin",
                label="Sign in",
                page_type="auth",
                context={
                    "headings": ["Sign in"],
                    "primary_actions": ["Sign in", "Forgot password?"],
                    "forms": [
                        {"tag": "input", "type": "email", "name": "email"},
                        {"tag": "input", "type": "password", "name": "password"},
                    ],
                },
            ),
        ],
    )


def _fake_auth_crawl(**kwargs: Any) -> dict[str, Any]:
    return _write_fake_manifest(
        output_path=kwargs["output_path"],
        start_url=kwargs["start_url"],
        phase="authenticated",
        require_login=True,
        routes=[
            _fake_route(
                start_url=kwargs["start_url"],
                phase="authenticated",
                require_login=True,
                path="/dashboard",
                label="Dashboard",
                page_type="dashboard",
            )
        ],
    )


def _auth_crawl_spy(calls: list[dict[str, Any]]):
    def _wrapped(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _fake_auth_crawl(**kwargs)

    return _wrapped


def _fake_route(
    *,
    start_url: str,
    phase: str,
    require_login: bool,
    path: str,
    label: str,
    page_type: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"sample_{phase}_{path.strip('/') or 'home'}",
        "label": label,
        "url": f"{start_url.rstrip('/')}{'' if path == '/' else path}",
        "path": path,
        "query": "",
        "depth": 0,
        "page_type": page_type,
        "phase": phase,
        "require_login": require_login,
        "navigation_steps": [
            "I open the configured home page",
            f'I navigate to "{label}"',
        ],
        "assertions": [
            f'The browser URL should include "{path}"',
            f'The page title or primary heading should show "{label}"',
        ],
        "validation_mode": "url",
        "context": context or {"headings": [label], "primary_actions": [], "forms": []},
    }


def _write_fake_manifest(
    *,
    output_path: str,
    start_url: str,
    phase: str,
    require_login: bool,
    routes: list[dict[str, Any]],
) -> dict[str, Any]:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1.0",
        "generated_at": "2026-04-17T00:00:00Z",
        "start_url": start_url,
        "base_origin": start_url,
        "crawl_options": {
            "phase": phase,
            "sut_profile": "generic",
            "crawl_authenticated": require_login,
        },
        "summary": {
            "route_count": len(routes),
            "visited_count": len(routes),
            "pending_count": 0,
            "skipped_count": 0,
            "error_count": 0,
        },
        "routes": routes,
        "skipped_routes": [],
        "errors": [],
    }
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "manifest_path": str(destination),
        "route_count": len(routes),
        "visited_count": len(routes),
        "pending_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "context_budget": {"compacted": False},
    }


if __name__ == "__main__":
    main()
