"""Smoke test for the LLM-first action task discovery path."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from adk_playwright_agent.app.models import CommandResult
from adk_playwright_agent.tools import action_task_tools, draft_case_tools, page_summary_tools

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard
    yaml = None


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    worklist_result = action_task_tools.build_action_discovery_worklist(
        manifest_path="adk_playwright_agent/eval/fixtures/action_intent_manifest.json",
        output_path="adk_playwright_agent/.adk/action_worklist.json",
        site_name="sample",
    )
    print(json.dumps(worklist_result, indent=2))

    worklist_path = Path(worklist_result["output_path"])
    worklist = json.loads(worklist_path.read_text(encoding="utf-8"))
    canonical_paths = {route["canonical_path"] for route in worklist["routes"]}
    skipped_route_reasons = {item["reason"] for item in worklist["skipped_routes"]}
    teamview = next(route for route in worklist["routes"] if route["canonical_path"] == "/calendar/teamview")

    assert worklist_result["ok"] is True
    assert "/calendar/teamview" in canonical_paths
    assert "/calendar/teamview?department=1&date=2026-03" not in canonical_paths
    assert teamview["folded_variants"] == ["/calendar/teamview?department=1&date=2026-03"]
    assert worklist_result["folded_variant_count"] == 1
    assert "unsafe_route" in skipped_route_reasons

    previous_adapter = action_task_tools._ADAPTER
    try:
        action_task_tools._ADAPTER = _FakeActionDiscoveryAdapter()
        observation_result = action_task_tools.observe_task_pages_from_worklist(
            worklist_path="adk_playwright_agent/.adk/action_worklist.json",
            output_path="adk_playwright_agent/.adk/page_observations.index.json",
            observation_dir="adk_playwright_agent/.adk/page_observations",
            site_name="sample",
            headed=False,
            persistent=False,
        )
    finally:
        action_task_tools._ADAPTER = previous_adapter

    print(json.dumps(observation_result, indent=2))
    observation_index = json.loads(Path(observation_result["output_path"]).read_text(encoding="utf-8"))
    observation_files = [Path(path) for path in observation_index["observation_files"]]
    first_observation = _load_page_artifact(observation_files[0])

    assert observation_result["ok"] is True
    assert observation_result["observation_count"] == 3
    assert all(path.exists() for path in observation_files)
    assert all(path.suffix == ".yml" for path in observation_files)
    assert "llm_task_discovery" not in first_observation
    assert "intent_drafts" not in first_observation
    assert "visible_text" not in first_observation
    assert first_observation["page_snapshot"]["content"]
    assert first_observation["page_id"] == "page-001"

    previous_vertex = page_summary_tools._VERTEX
    try:
        page_summary_tools._VERTEX = _FakeVertexAdapter()
        summary_result = page_summary_tools.summarize_pages_with_vertex(
            observation_index_path="adk_playwright_agent/.adk/page_observations.index.json",
            summary_index_path="adk_playwright_agent/.adk/page_summaries.index.json",
            summary_output_dir="adk_playwright_agent/.adk/page_summaries",
            site_name="sample",
        )
    finally:
        page_summary_tools._VERTEX = previous_vertex

    print(json.dumps(summary_result, indent=2))
    summary_index = json.loads(Path(summary_result["summary_index_path"]).read_text(encoding="utf-8"))
    first_summary = json.loads(Path(summary_index["summaries"][0]["path"]).read_text(encoding="utf-8"))
    summary_payloads = [
        json.loads(Path(item["path"]).read_text(encoding="utf-8"))
        for item in summary_index["summaries"]
    ]

    assert summary_result["ok"] is True
    assert summary_result["summary_count"] == 3
    assert "plain_language_summary" in first_summary
    assert "navigation_steps" in first_summary
    assert any(payload["navigation_steps"] for payload in summary_payloads)
    assert 10 <= len(first_summary["plain_language_summary"]) <= 100

    previous_vertex = draft_case_tools._VERTEX
    try:
        draft_case_tools._VERTEX = _FakeVertexAdapter()
        draft_result = draft_case_tools.draft_test_ideas_with_vertex(
            observation_index_path="adk_playwright_agent/.adk/page_observations.index.json",
            summary_index_path="adk_playwright_agent/.adk/page_summaries.index.json",
            draft_index_path="adk_playwright_agent/.adk/page_drafts.index.json",
            draft_output_dir="adk_playwright_agent/.adk/page_drafts",
            site_name="sample",
        )
    finally:
        draft_case_tools._VERTEX = previous_vertex

    print(json.dumps(draft_result, indent=2))
    draft_index = json.loads(Path(draft_result["draft_index_path"]).read_text(encoding="utf-8"))
    first_draft_page = json.loads(Path(draft_index["draft_pages"][0]["path"]).read_text(encoding="utf-8"))
    draft_payloads = [
        json.loads(Path(item["path"]).read_text(encoding="utf-8"))
        for item in draft_index["draft_pages"]
    ]

    assert draft_result["ok"] is True
    assert draft_result["draft_page_count"] == 3
    assert "navigation_steps" in first_draft_page
    assert any(payload["navigation_steps"] for payload in draft_payloads)
    assert first_draft_page["drafts"]
    assert "notes_for_human" in first_draft_page["drafts"][0]

    backlog_result = draft_case_tools.merge_page_drafts(
        draft_index_path="adk_playwright_agent/.adk/page_drafts.index.json",
        output_path="adk_playwright_agent/.adk/draft_backlog.json",
        site_name="sample",
    )
    print(json.dumps(backlog_result, indent=2))
    backlog = json.loads(Path(backlog_result["output_path"]).read_text(encoding="utf-8"))
    backlog_by_category = {task["category"]: task for task in backlog["tasks"]}

    assert backlog_result["ok"] is True
    assert backlog_result["raw_draft_count"] == 4
    assert backlog_result["backlog_count"] == 3
    assert backlog_by_category["create"]["priority"] == "P0"
    assert backlog_by_category["filter"]["execution_policy"] == "execute_read_only"
    assert backlog_by_category["delete"]["execution_policy"] == "dry_run_open_confirm"
    assert backlog_by_category["delete"]["execution_order"] > backlog_by_category["create"]["execution_order"]


class _FakeActionDiscoveryAdapter:
    def __init__(self) -> None:
        self.current_url = ""
        self.cwd = Path(__file__).resolve().parents[2]

    def open_browser(self, base_url: str, session_name: str, headed: bool, persistent: bool):
        self.current_url = base_url
        return _result(url=base_url, title="Sample")

    def goto(self, session_name: str, url: str):
        self.current_url = url
        return _result(url=url, title=_title_for(url))

    def snapshot(self, session_name: str, depth: int | None = None):
        snapshot_relative = Path(".adk") / "snapshots" / f"{Path(self.current_url).name or 'home'}.yml"
        snapshot_path = self.cwd / snapshot_relative
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(_snapshot_text_for(self.current_url), encoding="utf-8")
        return _result(
            url=self.current_url,
            title=_title_for(self.current_url),
            snapshot_path=str(snapshot_relative),
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
            "visible_text": "Add employee First name Email Create Cancel New absence",
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
            "visible_text": "Team view Department New absence Team calendar rows",
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
        "visible_text": "Employees Search employees Add employee Export employees New absence Employee list",
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


def _snapshot_text_for(url: str) -> str:
    if url.endswith("/users/add"):
        return (
            '- heading "Add employee" [ref=e1]\n'
            '- textbox "First name" [ref=e2]\n'
            '- textbox "Email" [ref=e3]\n'
            '- button "Create" [ref=e4]\n'
            '- link "Cancel" [ref=e5]\n'
        )
    if url.endswith("/calendar/teamview"):
        return (
            '- heading "Team view" [ref=e1]\n'
            '- combobox "Department" [ref=e2]\n'
            '- button "New absence" [ref=e3]\n'
            '- table [ref=e4]: Team calendar rows\n'
        )
    return (
        '- heading "Employees" [ref=e1]\n'
        '- searchbox "Search employees" [ref=e2]\n'
        '- link "Add employee" [ref=e3]\n'
        '- link "Export employees" [ref=e4]\n'
        '- button "New absence" [ref=e5]\n'
    )


class _FakeVertexAdapter:
    def generate_json(self, *, prompt: str, temperature: float = 0.2, max_output_tokens: int = 4096):
        is_users_add = _prompt_has_route(prompt, "/users/add")
        is_teamview = _prompt_has_route(prompt, "/calendar/teamview")
        if '"drafts"' in prompt:
            if is_users_add:
                return {
                    "ok": True,
                    "data": {
                        "drafts": [
                            {
                                "title": "Create employee",
                                "goal": "建立新員工資料",
                                "category": "create",
                                "priority": "P0",
                                "risk": "state_changing_safe",
                                "rough_steps": ["Open Add employee page", "Fill required fields", "Submit the form"],
                                "evidence": ["Add employee heading is visible", "Create button is visible"],
                                "notes_for_human": ["Need disposable test data"],
                                "dedupe_key": "sample|create|employee|/users/add",
                            }
                        ]
                    },
                }
            if is_teamview:
                return {
                    "ok": True,
                    "data": {
                        "drafts": [
                            {
                                "title": "Filter team calendar by department",
                                "goal": "依部門篩選團隊行事曆",
                                "category": "filter",
                                "priority": "P1",
                                "risk": "read_only",
                                "rough_steps": ["Open Team view", "Use the Department control"],
                                "evidence": ["Department control is visible"],
                                "notes_for_human": ["Result assertion still needs human review"],
                            }
                        ]
                    },
                }
            return {
                "ok": True,
                "data": {
                    "drafts": [
                        {
                            "title": "Create employee",
                            "goal": "從員工清單進入新增員工流程",
                            "category": "create",
                            "priority": "P0",
                            "risk": "state_changing_safe",
                            "rough_steps": ["Open Employees page", "Click Add employee"],
                            "evidence": ["Add employee link is visible"],
                            "notes_for_human": ["This is only the entry draft, not the full form submission"],
                            "dedupe_key": "sample|create|employee|/users/add",
                        },
                    {
                        "title": "Delete employee",
                        "goal": "確認是否可進入刪除員工流程",
                        "category": "delete",
                            "priority": "P0",
                            "risk": "state_changing_destructive",
                            "rough_steps": ["Open Employees page", "Open a delete entrypoint if visible"],
                        "evidence": ["Employee list implies row-level actions may exist"],
                        "notes_for_human": ["Only execute after human review"],
                    },
                    {
                        "title": "Navigate to home page",
                        "goal": "確認可透過全域導覽返回首頁",
                        "category": "navigate",
                        "priority": "P2",
                        "risk": "read_only",
                        "rough_steps": ["Click the Home link"],
                        "evidence": ["Global navigation includes a Home link"],
                        "notes_for_human": ["Should be filtered because route navigation is already covered elsewhere"],
                    },
                ]
            },
        }
        if "plain_language_summary" in prompt:
            if is_users_add:
                return {
                    "ok": True,
                    "data": {
                        "plain_language_summary": "這頁主要用來新增員工資料，會看到基本欄位和建立按鈕。",
                        "page_purpose": "Create a new employee record.",
                        "main_entities": ["employee"],
                        "key_forms": ["employee form"],
                        "key_actions": ["Create", "Cancel"],
                        "likely_user_goals": ["create employee"],
                        "risk_notes": ["submitting changes state"],
                        "evidence": ["heading: Add employee", "button: Create"],
                    },
                }
            if is_teamview:
                return {
                    "ok": True,
                    "data": {
                        "plain_language_summary": "這頁看起來是在查看團隊行事曆，並可依部門切換內容。",
                        "page_purpose": "Inspect the team calendar.",
                        "main_entities": ["team calendar", "department"],
                        "key_forms": ["department filter"],
                        "key_actions": ["Department", "New absence"],
                        "likely_user_goals": ["filter team calendar"],
                        "risk_notes": [],
                        "evidence": ["heading: Team view", "label: Department"],
                    },
                }
            return {
                "ok": True,
                "data": {
                    "plain_language_summary": "這頁主要是在看員工清單，也能搜尋或進一步操作員工資料。",
                    "page_purpose": "Browse employees.",
                    "main_entities": ["employee"],
                    "key_forms": ["employee search"],
                    "key_actions": ["Search", "Add employee", "Export employees"],
                    "likely_user_goals": ["search employees", "open employee creation"],
                    "risk_notes": ["export is an external side effect"],
                    "evidence": ["heading: Employees", "action: Add employee"],
                },
            }
        return {"ok": False, "error": "unexpected_prompt", "message": "Prompt shape not recognized by fake adapter."}


def _load_page_artifact(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        decoded = yaml.safe_load(text)
        if isinstance(decoded, dict):
            return decoded
    return json.loads(text)


def _prompt_has_route(prompt: str, route: str) -> bool:
    markers = (
        f'"route": "{route}"',
        f"route: {route}",
        f'"canonical_path": "{route}"',
        f"canonical_path: {route}",
    )
    return any(marker in prompt for marker in markers)


if __name__ == "__main__":
    main()
