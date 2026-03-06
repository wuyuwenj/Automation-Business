"""Creative copy generation tool backed by direct OpenAI generation."""

import json
import os

from ..openai_utils import generate_json


def generate_copy_impl(
    product: str,
    audience: str = "general",
    tone: str = "professional",
) -> dict:
    """Generate copy assets for a product or offer."""
    system_prompt = (
        "You are an elite direct-response copywriter. Generate polished, concrete "
        "marketing copy tailored to the audience and tone. Return valid JSON only "
        "with exactly these keys: headlines (5 strings), taglines (3 strings), "
        "ctas (3 strings), social_posts (2 strings). Do not include markdown."
    )
    user_prompt = (
        f"Product: {product}\n"
        f"Audience: {audience}\n"
        f"Tone: {tone}\n\n"
        "Generate campaign-ready marketing copy."
    )

    try:
        payload = generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model_id=os.getenv("MODEL_ID", "gpt-4o-mini"),
            max_tokens=900,
        )
        return {
            "status": "success",
            "content": [{"text": json.dumps(payload, indent=2)}],
            "copy": payload,
        }
    except Exception as exc:
        message = f"Copy generation failed: {exc}"
        return {
            "status": "error",
            "content": [{"text": message}],
            "copy": {},
        }
