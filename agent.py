"""ADK entry point for the Playwright test authoring scaffold."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

from adk_playwright_agent.app.prompts import ROOT_AGENT_INSTRUCTION
from adk_playwright_agent.tools.browser_tools import (
    click,
    close_browser,
    collect_page_inputs,
    collect_page_links,
    eval_js,
    fill,
    goto,
    login_from_notes,
    open_browser,
    press_key,
    save_storage_state,
    snapshot,
)
from adk_playwright_agent.tools.crawler_tools import (
    crawl_authenticated_site_to_manifest,
    crawl_site_to_manifest,
)
from adk_playwright_agent.tools.generator_tools import (
    generate_task_file,
    generate_tasks_from_manifest,
    write_route_manifest,
)
from adk_playwright_agent.tools.validation_tools import (
    validate_task_directory,
    validate_task_file,
)
from adk_playwright_agent.tools.workspace_tools import (
    list_files,
    read_text_file,
    write_text_file,
)

try:
    from google.adk.agents import Agent
    from google.adk.tools.function_tool import FunctionTool
except ImportError as exc:  # pragma: no cover - import guard for scaffolding
    Agent = None
    FunctionTool = None
    _ADK_IMPORT_ERROR = exc
else:
    _ADK_IMPORT_ERROR = None


def _build_root_agent():
    if Agent is None or FunctionTool is None:
        raise RuntimeError(
            "google-adk is not installed. Install project dependencies before loading the agent."
        ) from _ADK_IMPORT_ERROR

    model_name = os.getenv("ADK_MODEL", "gemini-2.5-flash")

    return Agent(
        name="playwright_test_author",
        model=model_name,
        description="Explores web apps with playwright-cli and generates browser test artifacts.",
        instruction=ROOT_AGENT_INSTRUCTION,
        tools=[
            FunctionTool(open_browser),
            FunctionTool(goto),
            FunctionTool(snapshot),
            FunctionTool(click),
            FunctionTool(fill),
            FunctionTool(press_key),
            FunctionTool(eval_js),
            FunctionTool(collect_page_links),
            FunctionTool(collect_page_inputs),
            FunctionTool(login_from_notes),
            FunctionTool(save_storage_state),
            FunctionTool(close_browser),
            FunctionTool(crawl_site_to_manifest),
            FunctionTool(crawl_authenticated_site_to_manifest),
            FunctionTool(list_files),
            FunctionTool(read_text_file),
            FunctionTool(write_text_file, require_confirmation=True),
            FunctionTool(write_route_manifest),
            FunctionTool(generate_task_file, require_confirmation=True),
            FunctionTool(generate_tasks_from_manifest),
            FunctionTool(validate_task_file),
            FunctionTool(validate_task_directory),
        ],
    )


root_agent = _build_root_agent() if Agent is not None else None


async def get_agent_async():
    """Return the ADK root agent for runtimes that expect an async factory."""

    if root_agent is None:
        raise RuntimeError(
            "google-adk is not installed. Install project dependencies before loading the agent."
        ) from _ADK_IMPORT_ERROR
    return root_agent


__all__ = ["get_agent_async", "root_agent"]
