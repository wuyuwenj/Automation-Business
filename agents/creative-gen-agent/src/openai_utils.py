"""Shared OpenAI helpers for creative generation tools."""

import json
import os
from typing import Any

import httpx


def _strip_fences(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1]
        if value.endswith("```"):
            value = value[:-3]
    return value.strip()


def _api_key() -> str:
    """Read the OpenAI API key from environment configuration."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    return api_key


def generate_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model_id: str,
    max_tokens: int,
) -> dict[str, Any]:
    """Generate a JSON object with the OpenAI chat completions HTTP API."""
    with httpx.Client(timeout=90.0) as client:
        response = client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        payload = response.json()
    content = (((payload.get("choices") or [{}])[0]).get("message") or {}).get("content") or "{}"
    return json.loads(_strip_fences(content))
