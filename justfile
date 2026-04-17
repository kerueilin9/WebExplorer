set shell := ["pwsh", "-NoLogo", "-Command"]

default:
    just --list

sync:
    uv sync

run:
    uv run adk run .

web:
    uv run adk web ..

lint:
    uv run python -m compileall agent.py app tools adapters scripts

manifest-test:
    uv run python scripts/manifest_smoke.py

context-test:
    uv run python scripts/context_memory_smoke.py

crawler-test:
    uv run python scripts/crawler_manifest_smoke.py

workflow-test:
    uv run python scripts/workflow_smoke.py

credentials-test:
    uv run python scripts/credentials_smoke.py

intent-test:
    uv run python scripts/intent_smoke.py
