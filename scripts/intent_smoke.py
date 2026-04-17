"""Smoke test for action intent extraction."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from adk_playwright_agent.tools.intent_tools import extract_action_intents_from_manifest


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    result = extract_action_intents_from_manifest(
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


if __name__ == "__main__":
    main()
