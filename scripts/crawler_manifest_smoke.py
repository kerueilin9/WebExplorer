"""Smoke checks for crawler manifest helper behavior without launching a browser."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from adk_playwright_agent.tools.crawler_tools import (
    _build_navigation_steps,
    _classify_page_type,
    _full_url,
    _login_script,
    _manifest_write_destination,
    _normalize_candidate_link,
    _route_id,
    _route_label,
)
from adk_playwright_agent.app.context_memory import PageSummary


def main() -> None:
    base_origin = "http://localhost:3101"
    current_url = f"{base_origin}/"
    accepted = _normalize_candidate_link(
        raw_link={
            "text": "Project Alpha",
            "href": "http://localhost:3101/projects/42?utm_source=test",
        },
        current_url=current_url,
        base_origin=base_origin,
        same_origin_only=True,
        include_patterns=[],
        exclude_patterns=[],
    )
    external = _normalize_candidate_link(
        raw_link={"text": "External", "href": "https://example.com/"},
        current_url=current_url,
        base_origin=base_origin,
        same_origin_only=True,
        include_patterns=[],
        exclude_patterns=[],
    )
    destructive = _normalize_candidate_link(
        raw_link={"text": "Delete record", "href": "/records/1/delete"},
        current_url=current_url,
        base_origin=base_origin,
        same_origin_only=True,
        include_patterns=[],
        exclude_patterns=[],
    )
    rss = _normalize_candidate_link(
        raw_link={"text": "RSS", "href": "/activity.rss"},
        current_url=current_url,
        base_origin=base_origin,
        same_origin_only=True,
        include_patterns=[],
        exclude_patterns=[],
    )
    signout = _normalize_candidate_link(
        raw_link={"text": "Sign Out", "href": "/keystone/signout"},
        current_url=current_url,
        base_origin=base_origin,
        same_origin_only=True,
        include_patterns=[],
        exclude_patterns=[],
    )
    trailing_slash = _normalize_candidate_link(
        raw_link={"text": "Record Details", "href": "/records/1/"},
        current_url=current_url,
        base_origin=base_origin,
        same_origin_only=True,
        include_patterns=[],
        exclude_patterns=[],
    )
    parents = {
        "/": {"source_path": None, "label": "Home"},
        "/projects/42": {
            "source_path": "/",
            "label": "Project Alpha",
        },
        "/records/1": {
            "source_path": "/projects/42",
            "label": "Record Details",
        },
    }

    assert accepted is not None
    assert accepted["path"] == "/projects/42"
    assert external is None
    assert destructive is None
    assert rss is None
    assert signout is None
    assert trailing_slash is not None
    assert trailing_slash["path"] == "/records/1"
    assert _classify_page_type("/projects/42") == "detail"
    assert _classify_page_type("/admin/settings") == "admin_settings"
    assert _classify_page_type("/account/profile") == "account"
    assert _classify_page_type("/records/new") == "create"
    assert _classify_page_type("/discussion/1/welcome") == "detail"
    assert _full_url(base_origin, "/activity?filter=new") == "http://localhost:3101/activity?filter=new"
    assert _route_id("localhost_3101", "detail", "/records/1").startswith(
        "localhost_3101_guest_detail_"
    )
    assert (
        _route_label(
            "/records/1",
            PageSummary(url=current_url, headings=["導覽", "Example App", "Fallback"]),
            parents,
        )
        == "Record Details"
    )
    assert (
        _route_label(
            "/records/1",
            PageSummary(url=current_url, headings=["導覽", "Record Details"]),
            {
                "/records/1": {
                    "source_path": "/",
                    "label": "2019年6月10日 下午10:24",
                }
            },
        )
        == "Record Details"
    )

    steps = _build_navigation_steps("/records/1", parents)
    assert steps == [
        "I open the configured home page",
        'I click the "Project Alpha" link to reach "/projects/42"',
        'I click the "Record Details" link to reach "/records/1"',
    ]
    assert "already_authenticated_redirect" in _login_script("demo", "secret")

    with TemporaryDirectory() as temp_dir:
        destination = Path(temp_dir) / "route_manifest.json"
        destination.write_text(
            json.dumps({"summary": {"route_count": 3}, "routes": [{}, {}, {}]}),
            encoding="utf-8",
        )
        failed_destination = _manifest_write_destination(destination, ok=False)
        assert failed_destination != destination
        assert failed_destination.name.startswith("route_manifest.failed.")

    print(
        json.dumps(
            {
                "accepted_path": accepted["path"],
                "external_skipped": external is None,
                "destructive_skipped": destructive is None,
                "signout_skipped": signout is None,
                "rss_skipped": rss is None,
                "navigation_step_count": len(steps),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
