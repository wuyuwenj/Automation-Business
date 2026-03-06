"""Shared Gemini helpers for structured generation."""

import json
import os
from typing import Any

import httpx


def _api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required")
    return api_key


def generate_json(
    *,
    system_instruction: str,
    user_prompt: str,
    model_id: str,
    response_schema: dict[str, Any],
) -> dict[str, Any]:
    """Generate structured JSON with the Gemini REST API."""
    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
            headers={
                "x-goog-api-key": _api_key(),
                "Content-Type": "application/json",
            },
            json={
                "system_instruction": {
                    "parts": [{"text": system_instruction}],
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": user_prompt}],
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": response_schema,
                    "temperature": 0.4,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()

    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")

    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response")
    return json.loads(text)
