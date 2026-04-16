"""Smoke test for crawler context memory behavior."""

from __future__ import annotations

import json

from adk_playwright_agent.app.context_memory import (
    CredentialReference,
    CrawlerContext,
    PageSummary,
)


def main() -> None:
    context = CrawlerContext()
    context.long_term_memory.final_goal = "Explore the target SUT and write a route manifest."
    context.long_term_memory.target_app = "example_sut"
    context.long_term_memory.remember_credentials(
        CredentialReference(
            system_name="example_sut",
            username="demo@example.test",
            credentials_source="D:/Ker/Desktop/Document/other/GUI_test/passwords.txt",
            storage_state_path="D:/Ker/Desktop/Document/other/GUI_test/.auth/example_sut_state.json",
            verified_at="2026-04-16T00:00:00Z",
        )
    )

    context.task_state.start_url = "http://localhost:3101/"
    context.task_state.add_visited("/")
    for index in range(120):
        path = f"/sample-route-{index}"
        context.task_state.add_pending(path)
        context.task_state.record_route_parent(path, source_path="/", label=f"Route {index}")

    context.set_current_page(
        PageSummary(
            url="http://localhost:3101/projects",
            title="Projects | Example SUT",
            headings=["Projects"],
            primary_actions=["Login", "Register", "Search", "Dashboard", "Settings"],
            links_sample=[
                {"text": f"Route {index}", "path": f"/sample-route-{index}"}
                for index in range(300)
            ],
            forms=[{"name": "search", "placeholder": "Search"}],
            snapshot_artifact=".adk/snapshots/projects.json",
        )
    )

    for index in range(5):
        context.record_operation_feedback(
            action="click",
            target=f"text=Route {index}",
            ok=True,
            url_before="/",
            url_after=f"/sample-route-{index}",
            message="navigation completed",
        )

    context.record_operation_feedback(
        action="click",
        target="text=Delete",
        ok=False,
        url_before="/projects/42",
        message="blocked destructive action",
        error_type="blocked_by_policy",
    )
    context.record_operation_feedback(
        action="click",
        target="text=Delete",
        ok=False,
        url_before="/projects/42",
        message="blocked destructive action",
        error_type="blocked_by_policy",
    )

    compact_pack = context.build_context_pack(max_context_tokens=1_200)
    serialized_pack = json.dumps(compact_pack, ensure_ascii=False)

    assert len(context.working_memory.recent_feedback) == 3
    assert context.is_action_blocked(
        "/projects/42",
        "click",
        "text=Delete",
    )
    assert compact_pack["context_budget"]["compacted"] is True
    assert "raw_password" not in serialized_pack

    print(
        json.dumps(
            {
                "recent_feedback_count": len(context.working_memory.recent_feedback),
                "blocked_action_count": len(context.long_term_memory.blocked_actions),
                "compaction": compact_pack["context_budget"],
                "next_candidate_count": len(compact_pack["task_state"]["next_candidates"]),
                "link_sample_count": len(
                    compact_pack["working_memory"]["current_page"]["links_sample"]
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
