# ADK Playwright Agent Scaffold

This directory contains a starter scaffold for an Agent Development Kit (ADK) project that can:

- drive `playwright-cli` through structured tools
- explore a target web application
- read and write project files
- generate route manifests and task JSON files
- validate generated task outputs

This is a project skeleton. It includes real module boundaries and usable tool wrappers, but the orchestration prompt and output generation logic are still intentionally conservative.

## Layout

```text
adk_playwright_agent/
  agent.py
  app/
  tools/
  adapters/
  templates/
  eval/
  pyproject.toml
```

## Install

```powershell
cd D:\Ker\Desktop\Document\other\GUI_test\adk_playwright_agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Configuration

Copy `.env.example` and adjust values if needed.

Important settings:

- `ADK_MODEL`
- `AGENT_WORKSPACE_ROOT`
- `PLAYWRIGHT_CLI_BIN`
- `DEFAULT_CREDENTIALS_FILE`

## ADK Entry Point

The ADK entry point is [agent.py](/D:/Ker/Desktop/Document/other/GUI_test/adk_playwright_agent/agent.py).

It exposes:

- `root_agent`
- `get_agent_async()`

## Current Tool Groups

- Browser tools
- Workspace tools
- Generator tools
- Validation tools

## Next Implementation Slice

Recommended next steps:

1. Install `google-adk` and verify ADK discovery loads `root_agent`.
2. Run a single prompt that only generates a route manifest.
3. Tighten the root prompt for your preferred task format.
4. Add project-specific output generators once one target app is stable.
