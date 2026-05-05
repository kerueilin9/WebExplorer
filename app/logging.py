"""Logging helpers for the agent."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from adk_playwright_agent.app.policies import workspace_root


_DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s - %(message)s"


def configure_logging(
    *,
    log_level: str | int = "INFO",
    log_dir: str = "logs",
    log_file: str = "adk_playwright_agent.log",
    enable_console: bool = True,
    enable_file: bool = True,
) -> Path | None:
    """Configure a shared logging setup for console and file outputs."""

    level = _normalize_level(os.getenv("ADK_LOG_LEVEL", str(log_level)))
    log_dir = os.getenv("ADK_LOG_DIR", log_dir)
    log_file = os.getenv("ADK_LOG_FILE", log_file)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if getattr(root_logger, "_adk_logging_configured", False):
        return _log_path(log_dir, log_file) if enable_file else None

    formatter = logging.Formatter(_DEFAULT_LOG_FORMAT)
    handlers: list[logging.Handler] = []

    if enable_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    log_path: Optional[Path] = None
    if enable_file:
        log_path = _log_path(log_dir, log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    for handler in handlers:
        root_logger.addHandler(handler)

    root_logger._adk_logging_configured = True  # type: ignore[attr-defined]
    return log_path


def _normalize_level(value: str | int) -> int:
    if isinstance(value, int):
        return value
    candidate = str(value).strip().upper()
    if not candidate:
        return logging.INFO
    return logging._nameToLevel.get(candidate, logging.INFO)


def _log_path(log_dir: str, log_file: str) -> Path:
    return workspace_root() / log_dir / log_file
