"""Smoke test for action intent extraction."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from adk_playwright_agent.app.models import CommandResult
from adk_playwright_agent.tools.generator_tools import (
    _action_intent_has_low_value_label,
    generate_action_tasks_from_intents,
)
from adk_playwright_agent.tools import intent_tools
from adk_playwright_agent.tools.validation_tools import validate_task_directory
from adk_playwright_agent.tools.workflow_tools import run_action_review_task_workflow


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    result = intent_tools.extract_action_intents_from_manifest(
        manifest_path="adk_playwright_agent/eval/fixtures/action_intent_manifest.json",
        output_path="adk_playwright_agent/.adk/action_intents.json",
        site_name="sample",
    )
    print(json.dumps(result, indent=2))

    output_path = Path(result["output_path"])
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    intent_types = {intent["intent_type"] for intent in payload["intents"]}
    skipped_reasons = {item["reason"] for item in payload["skipped_candidates"]}

    assert result["ok"] is True
    assert "search" in intent_types
    assert "create" in intent_types
    assert "high_risk" in skipped_reasons

    worklist_result = intent_tools.build_action_discovery_worklist(
        manifest_path="adk_playwright_agent/eval/fixtures/action_intent_manifest.json",
        output_path="adk_playwright_agent/.adk/action_worklist.json",
        site_name="sample",
    )
    print(json.dumps(worklist_result, indent=2))

    worklist_path = Path(worklist_result["output_path"])
    worklist = json.loads(worklist_path.read_text(encoding="utf-8"))
    canonical_paths = {route["canonical_path"] for route in worklist["routes"]}
    skipped_route_reasons = {item["reason"] for item in worklist["skipped_routes"]}
    teamview = next(
        route for route in worklist["routes"] if route["canonical_path"] == "/calendar/teamview"
    )

    assert worklist_result["ok"] is True
    assert "/calendar/teamview" in canonical_paths
    assert "/calendar/teamview?department=1&date=2026-03" not in canonical_paths
    assert teamview["folded_variants"] == ["/calendar/teamview?department=1&date=2026-03"]
    assert worklist_result["folded_variant_count"] == 1
    assert "unsafe_route" in skipped_route_reasons

    previous_adapter = intent_tools._ADAPTER
    try:
        intent_tools._ADAPTER = _FakeActionDiscoveryAdapter()
        browser_result = intent_tools.discover_page_actions_from_worklist(
            worklist_path="adk_playwright_agent/.adk/action_worklist.json",
            output_path="adk_playwright_agent/.adk/action_intents.browser.json",
            evidence_dir="adk_playwright_agent/.adk/action_discovery",
            site_name="sample",
            headed=False,
            persistent=False,
        )
    finally:
        intent_tools._ADAPTER = previous_adapter

    print(json.dumps(browser_result, indent=2))
    browser_output = json.loads(Path(browser_result["output_path"]).read_text(encoding="utf-8"))
    browser_intent_types = {intent["intent_type"] for intent in browser_output["intents"]}
    browser_labels = {intent["label"] for intent in browser_output["intents"]}
    browser_skipped_reasons = {item["reason"] for item in browser_output["skipped_candidates"]}
    evidence_paths = [Path(path) for path in browser_output["evidence_files"]]

    assert browser_result["ok"] is True
    assert browser_result["route_count"] == 3
    assert browser_result["intent_count"] == 5
    assert "search" in browser_intent_types
    assert "create" in browser_intent_types
    assert "filter" in browser_intent_types
    assert "Add employee" in browser_labels
    assert "New absence" in browser_labels
    assert "global_duplicate" in browser_skipped_reasons
    assert all(path.exists() for path in evidence_paths)

    review_packet_result = intent_tools.prepare_action_intent_review_packets(
        intents_path="adk_playwright_agent/.adk/action_intents.browser.json",
        output_dir="adk_playwright_agent/.adk/action_review_packets",
        clear_existing=True,
    )
    review_decisions = []
    for intent in browser_output["intents"]:
        decision = {
            "intent_id": intent["intent_id"],
            "decision": "accept",
            "review_notes": "Accepted from route-scoped browser evidence.",
        }
        if intent["label"] == "New absence":
            decision["label"] = "Request absence"
            decision["review_notes"] = "Renamed to a clearer workflow label from visible evidence."
            decision["workflow_steps"] = [
                'I open the "Request absence" create workflow.',
                'I fill "From date" with "2026-05-04".',
                'I fill "To date" with "2026-05-04".',
                'I fill "Reason" with "Reviewed action workflow smoke test".',
                'I submit the request.',
            ]
            decision["test_data"] = {
                "From date": "2026-05-04",
                "To date": "2026-05-04",
                "Reason": "Reviewed action workflow smoke test",
            }
            decision["commit_policy"] = "Submit is allowed because this reviewed create workflow is intended to create test data."
            decision["success_evidence"] = [
                "The absence request should be visible or a confirmation should be shown.",
                "The workflow should not show validation errors for valid input.",
            ]
        review_decisions.append(decision)
    review_decisions.append(
        {
            "intent_id": "missing_intent",
            "decision": "accept",
            "review_notes": "This should be rejected by the constrained writer.",
        }
    )
    reviewed_result = intent_tools.write_reviewed_action_intents(
        source_intents_path="adk_playwright_agent/.adk/action_intents.browser.json",
        reviewed_intents_json=json.dumps({"reviewed_intents": review_decisions}),
        output_path="adk_playwright_agent/.adk/action_intents.reviewed.json",
        reviewer_name="smoke_llm_reviewer",
    )
    reviewed_output = json.loads(Path(reviewed_result["output_path"]).read_text(encoding="utf-8"))
    reviewed_labels = {intent["label"] for intent in reviewed_output["intents"]}
    review_skip_reasons = {item["reason"] for item in reviewed_output["review_skips"]}

    assert review_packet_result["ok"] is True
    assert review_packet_result["packet_count"] == 3
    assert reviewed_result["reviewed_intent_count"] == 5
    assert "Request absence" in reviewed_labels
    assert "unknown_intent_id" in review_skip_reasons

    save_candidate = intent_tools._candidate_from_text(
        text="Save changes to department",
        label="Save changes to department",
        route={"path": "/settings/departments/edit/1", "phase": "authenticated"},
        site_name="sample",
        route_id="sample_authenticated_departments_edit",
        entry_path="/settings/departments/edit/1",
        fields=[],
        source="primary_action",
    )
    report_update_candidate = intent_tools._candidate_from_text(
        text="Update results department",
        label="Update results",
        route={"path": "/reports/allowancebytime", "phase": "authenticated"},
        site_name="sample",
        route_id="sample_authenticated_reports_allowancebytime",
        entry_path="/reports/allowancebytime",
        fields=["Department"],
        source="primary_action",
    )

    assert save_candidate is not None
    assert save_candidate["intent_type"] == "edit"
    assert report_update_candidate is not None
    assert report_update_candidate["intent_type"] == "filter"
    assert report_update_candidate["safety_level"] == "read_only"

    action_task_result = generate_action_tasks_from_intents(
        intents_path="adk_playwright_agent/.adk/action_intents.reviewed.json",
        output_dir="adk_playwright_agent/.adk/generated_action_tasks",
        site_name="sample",
        storage_state_path=".auth/sample_state.json",
        clear_existing=True,
    )
    action_task_validation = validate_task_directory(
        directory="adk_playwright_agent/.adk/generated_action_tasks",
        expected_start_url="http://localhost:3102",
    )
    print(json.dumps(action_task_result, indent=2))
    print(json.dumps(action_task_validation, indent=2))

    assert action_task_result["ok"] is True
    action_task_skipped_reasons = {item["reason"] for item in action_task_result["skipped"]}
    assert action_task_result["generated_count"] == 4
    assert "covered_by_target_route_intent" in action_task_skipped_reasons
    assert action_task_validation["total_files"] == 4
    assert action_task_validation["invalid_files"] == 0
    request_task_path = Path(action_task_result["generated"][1]["path"])
    request_task = json.loads(request_task_path.read_text(encoding="utf-8"))
    request_steps = request_task["gherkin"]["when"]
    request_assertions = request_task["gherkin"]["then"]
    assert any("I submit the request." == step for step in request_steps)
    assert all("stop before submitting" not in step.lower() for step in request_steps)
    assert "The absence request should be visible or a confirmation should be shown." in request_assertions
    assert _action_intent_has_low_value_label({"label": "1"}) is True
    assert _action_intent_has_low_value_label({"label": "/users/edit/1/schedule"}) is True
    assert _action_intent_has_low_value_label({"label": "Save changes"}) is False

    workflow_pending = run_action_review_task_workflow(
        intents_path="adk_playwright_agent/.adk/action_intents.browser.json",
        output_root="adk_playwright_agent/.adk/action_workflow_pending",
        site_name="sample",
        storage_state_path=".auth/sample_state.json",
        clear_existing=True,
    )
    workflow_completed = run_action_review_task_workflow(
        intents_path="adk_playwright_agent/.adk/action_intents.browser.json",
        output_root="adk_playwright_agent/.adk/action_workflow_completed",
        site_name="sample",
        storage_state_path=".auth/sample_state.json",
        reviewed_intents_json=json.dumps({"reviewed_intents": review_decisions}),
        clear_existing=True,
    )

    assert workflow_pending["ok"] is True
    assert workflow_pending["summary"]["needs_review"] is True
    assert workflow_pending["summary"]["review_packet_count"] == 3
    assert workflow_pending["phases"]["action_tasks"]["skipped"] is True
    assert workflow_completed["ok"] is True
    assert workflow_completed["summary"]["reviewed_intent_count"] == 5
    assert workflow_completed["summary"]["action_generated_count"] == 4
    assert workflow_completed["summary"]["action_valid_files"] == 4


class _FakeActionDiscoveryAdapter:
    def __init__(self) -> None:
        self.current_url = ""

    def open_browser(self, base_url: str, session_name: str, headed: bool, persistent: bool):
        self.current_url = base_url
        return _result(url=base_url, title="Sample")

    def goto(self, session_name: str, url: str):
        self.current_url = url
        return _result(url=url, title=_title_for(url))

    def snapshot(self, session_name: str, depth: int | None = None):
        return _result(
            url=self.current_url,
            title=_title_for(self.current_url),
            snapshot_path=f".adk/snapshots/{Path(self.current_url).name or 'home'}.json",
        )

    def eval_js(self, session_name: str, script: str, raw: bool):
        return _result(
            url=self.current_url,
            title=_title_for(self.current_url),
            raw_value=_page_data_for(self.current_url),
        )

    def load_storage_state(self, session_name: str, path: str):
        return _result(url=self.current_url, title=_title_for(self.current_url))

    def close_browser(self, session_name: str):
        return _result(url=self.current_url, title=_title_for(self.current_url))


def _result(
    *,
    url: str = "",
    title: str = "",
    snapshot_path: str | None = None,
    raw_value=None,
) -> CommandResult:
    return CommandResult(
        command=["fake"],
        returncode=0,
        stdout="",
        stderr="",
        url=url,
        title=title,
        snapshot_path=snapshot_path,
        raw_value=raw_value,
    )


def _title_for(url: str) -> str:
    if url.endswith("/users/add"):
        return "Add employee"
    if url.endswith("/calendar/teamview"):
        return "Team view"
    return "Employees"


def _page_data_for(url: str) -> dict:
    if url.endswith("/users/add"):
        return {
            "url": url,
            "title": "Add employee",
            "headings": ["Add employee"],
            "forms": [
                {"tag": "input", "type": "text", "name": "firstname", "label": "First name"},
                {"tag": "input", "type": "email", "name": "email", "label": "Email"},
            ],
            "controls": [
                {"label": "New absence", "tag": "button", "selector": "#book_time_off_btn"},
                {"label": "Create", "tag": "button", "type": "submit", "selector": "button[type=submit]"},
                {"label": "Cancel", "tag": "a", "path": "/users", "same_origin": True},
            ],
            "tables": [],
        }
    if url.endswith("/calendar/teamview"):
        return {
            "url": url,
            "title": "Team view",
            "headings": ["Team view"],
            "forms": [
                {"tag": "select", "type": "", "name": "department", "label": "Department"},
            ],
            "controls": [
                {"label": "New absence", "tag": "button", "selector": "#book_time_off_btn"},
                {"label": "Department", "tag": "select", "selector": "select[name=department]"},
            ],
            "tables": [{"text": "Team calendar rows"}],
        }
    return {
        "url": url,
        "title": "Employees",
        "headings": ["Employees"],
        "forms": [
            {"tag": "input", "type": "search", "name": "q", "label": "Search employees"},
        ],
        "controls": [
            {"label": "New absence", "tag": "button", "selector": "#book_time_off_btn"},
            {"label": "Search", "tag": "button", "selector": "button.search"},
            {"label": "Add employee", "tag": "a", "path": "/users/add", "same_origin": True},
            {"label": "Export employees", "tag": "a", "path": "/users/export", "same_origin": True},
        ],
        "tables": [{"text": "Employee list"}],
    }


if __name__ == "__main__":
    main()
