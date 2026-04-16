"""Smoke test for route manifest generation."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from adk_playwright_agent.tools.generator_tools import (
    generate_tasks_from_manifest,
    write_route_manifest,
)
from adk_playwright_agent.tools.validation_tools import validate_task_directory


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    routes_path = project_root / "eval" / "fixtures" / "sample_routes.json"
    output_path = "adk_playwright_agent/.adk/sample_manifest.json"

    routes = json.loads(routes_path.read_text(encoding="utf-8"))
    result = write_route_manifest(output_path=output_path, routes_json=json.dumps(routes))

    print(json.dumps({"manifest_result": result}, indent=2))

    task_result = generate_tasks_from_manifest(
        manifest_path=output_path,
        output_dir="adk_playwright_agent/.adk/generated_tasks",
        site_name="sample",
        start_url="http://localhost:3101",
        task_id_prefix="sample",
        max_tasks=1,
    )
    print(json.dumps({"task_generation_result": task_result}, indent=2))

    validation = validate_task_directory(
        directory="adk_playwright_agent/.adk/generated_tasks",
        expected_start_url="http://localhost:3101",
    )
    print(json.dumps({"validation_probe": validation}, indent=2))


if __name__ == "__main__":
    main()
