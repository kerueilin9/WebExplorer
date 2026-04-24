"""Thin Vertex AI GenAI adapter for page understanding workflows."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class VertexGenAIAdapter:
    """Call Gemini on Vertex AI using google-genai when configured."""

    def __init__(
        self,
        *,
        model: str | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        self.model = model or os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT", "")
        self.location = location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    def generate_json(
        self,
        *,
        prompt: str,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Generate a JSON object from Vertex AI and parse the result."""

        if not self.project:
            return self._error(
                "missing_project",
                "GOOGLE_CLOUD_PROJECT is not configured.",
            )
        if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            return self._error(
                "missing_credentials",
                "GOOGLE_APPLICATION_CREDENTIALS is not configured.",
            )

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            return self._error(
                "missing_dependency",
                f"google-genai is not installed: {exc}",
            )

        try:
            client = genai.Client(
                vertexai=True,
                project=self.project,
                location=self.location,
                http_options=types.HttpOptions(api_version="v1"),
            )
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:  # pragma: no cover - network/service error surface
            return self._error("vertex_request_failed", str(exc))

        raw_text = getattr(response, "text", "") or ""
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return self._error(
                "invalid_model_json",
                f"Model response was not valid JSON: {exc}",
                raw_text=raw_text,
            )
        if not isinstance(parsed, dict):
            return self._error(
                "invalid_model_payload",
                "Model response must decode to a JSON object.",
                raw_text=raw_text,
            )
        return {
            "ok": True,
            "model": self.model,
            "project": self.project,
            "location": self.location,
            "data": parsed,
            "raw_text": raw_text,
        }

    @staticmethod
    def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
        return {"ok": False, "error": error, "message": message, **extra}
