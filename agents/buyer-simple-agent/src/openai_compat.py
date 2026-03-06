"""Helpers for OpenAI-compatible model configuration."""

import os

from strands.models.openai import OpenAIModel


def validate_openai_config() -> str | None:
    """Validate env for OpenAI or an OpenAI-compatible gateway."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    bearer_token = os.getenv("OPENAI_BEARER_TOKEN", "")
    base_url = os.getenv("OPENAI_BASE_URL", "")

    if not api_key and not bearer_token:
        return (
            "OPENAI_API_KEY is required. For OpenAI-compatible gateways, "
            "set OPENAI_BEARER_TOKEN and OPENAI_BASE_URL instead."
        )

    if bearer_token and not base_url:
        return "OPENAI_BASE_URL is required when OPENAI_BEARER_TOKEN is set."

    return None


def build_openai_client_args() -> dict:
    """Build OpenAI client args, including proxy/gateway overrides."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    bearer_token = os.getenv("OPENAI_BEARER_TOKEN", "")
    base_url = os.getenv("OPENAI_BASE_URL", "")

    client_args = {
        # Some OpenAI-compatible gateways require a non-empty api_key argument
        # even when auth is supplied through a custom Authorization header.
        "api_key": api_key or "not-used",
    }
    if base_url:
        client_args["base_url"] = base_url
    if bearer_token:
        client_args["default_headers"] = {
            "Authorization": f"Bearer {bearer_token}",
        }
    return client_args


def create_openai_model() -> OpenAIModel:
    """Create the buyer's OpenAI-compatible model."""
    max_tokens = int(os.getenv("MAX_OUTPUT_TOKENS", "16384"))
    return OpenAIModel(
        client_args=build_openai_client_args(),
        model_id=os.getenv("MODEL_ID", "gpt-4o-mini"),
        params={"max_tokens": max_tokens},
    )
