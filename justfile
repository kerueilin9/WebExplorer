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
