"""Thin GenAI adapter for page understanding workflows."""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class VertexGenAIAdapter:
    """Call Gemini through Vertex AI or Gemini API using google-genai."""

    def __init__(
        self,
        *,
        model: str | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        self.use_vertexai = _read_bool_env("GOOGLE_GENAI_USE_VERTEXAI", default=True)
        self.model = model or _default_model(self.use_vertexai)
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT", "")
        self.location = location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.api_key = os.getenv("GOOGLE_API_KEY", "")
        self.max_retries = _read_int_env("VERTEX_MAX_RETRIES", default=5, minimum=1)
        self.retry_initial_seconds = _read_float_env(
            "VERTEX_RETRY_INITIAL_SECONDS",
            default=2.0,
            minimum=0.1,
        )
        self.retry_max_seconds = _read_float_env(
            "VERTEX_RETRY_MAX_SECONDS",
            default=20.0,
            minimum=self.retry_initial_seconds,
        )

    def generate_json(
        self,
        *,
        prompt: str,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Generate a JSON object from Vertex AI or Gemini API and parse the result."""

        if self.use_vertexai:
            if not self.project:
                return self._error(
                    "missing_project",
                    "GOOGLE_CLOUD_PROJECT is not configured.",
                    backend="vertex_ai",
                )
            if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                return self._error(
                    "missing_credentials",
                    "GOOGLE_APPLICATION_CREDENTIALS is not configured.",
                    backend="vertex_ai",
                )
        elif not self.api_key:
            return self._error(
                "missing_api_key",
                "GOOGLE_API_KEY is not configured.",
                backend="gemini_api",
            )

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            return self._error(
                "missing_dependency",
                f"google-genai is not installed: {exc}",
            )

        client = self._build_client(genai=genai, types=types)
        response = None
        last_error: Exception | None = None
        successful_attempt = 0
        for attempt in range(1, self.max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        response_mime_type="application/json",
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                last_error = None
                successful_attempt = attempt
                break
            except Exception as exc:  # pragma: no cover - network/service error surface
                last_error = exc
                if not self._is_retryable_resource_exhausted(exc) or attempt >= self.max_retries:
                    return self._error(
                        "vertex_request_failed",
                        str(exc),
                        attempts=attempt,
                        backend=self.backend_name,
                    )
                time.sleep(self._retry_delay(attempt))

        if response is None:
            return self._error(
                "vertex_request_failed",
                str(last_error or "Vertex request failed."),
                attempts=self.max_retries,
                backend=self.backend_name,
            )

        raw_text = getattr(response, "text", "") or ""
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return self._error(
                "invalid_model_json",
                f"Model response was not valid JSON: {exc}",
                raw_text=raw_text,
                backend=self.backend_name,
            )
        if not isinstance(parsed, dict):
            return self._error(
                "invalid_model_payload",
                "Model response must decode to a JSON object.",
                raw_text=raw_text,
                backend=self.backend_name,
            )
        return {
            "ok": True,
            "backend": self.backend_name,
            "model": self.model,
            "project": self.project,
            "location": self.location,
            "attempts": successful_attempt or 1,
            "data": parsed,
            "raw_text": raw_text,
        }

    @property
    def backend_name(self) -> str:
        return "vertex_ai" if self.use_vertexai else "gemini_api"

    def _build_client(self, *, genai, types):
        if self.use_vertexai:
            return genai.Client(
                vertexai=True,
                project=self.project,
                location=self.location,
                http_options=types.HttpOptions(api_version="v1"),
            )
        return genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(api_version="v1beta"),
        )

    def _retry_delay(self, attempt: int) -> float:
        base_delay = min(
            self.retry_max_seconds,
            self.retry_initial_seconds * (2 ** max(attempt - 1, 0)),
        )
        jitter = random.uniform(0.0, min(1.0, base_delay * 0.25))
        return min(self.retry_max_seconds, base_delay + jitter)

    @staticmethod
    def _is_retryable_resource_exhausted(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return True
        text = str(exc).upper()
        return "429" in text and "RESOURCE_EXHAUSTED" in text

    @staticmethod
    def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
        return {"ok": False, "error": error, "message": message, **extra}


def _read_int_env(name: str, *, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _read_float_env(name: str, *, default: float, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _read_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _default_model(use_vertexai: bool) -> str:
    if use_vertexai:
        return (
            os.getenv("VERTEX_MODEL")
            or os.getenv("GENAI_MODEL")
            or os.getenv("ADK_MODEL")
            or "gemini-2.5-flash"
        )
    return (
        os.getenv("GOOGLE_API_MODEL")
        or os.getenv("GENAI_MODEL")
        or os.getenv("ADK_MODEL")
        or "gemini-2.5-flash"
    )
