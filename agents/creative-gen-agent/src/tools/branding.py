"""Brand strategy generation tool backed by direct OpenAI generation."""

import json
import os

from ..openai_utils import generate_json


def generate_brand_impl(
    concept: str,
    industry: str = "tech",
    style: str = "modern",
) -> dict:
    """Generate a brand brief for a concept."""
    system_prompt = (
        "You are a senior brand strategist. Create clear, commercially useful brand "
        "briefs. Return valid JSON only with exactly these keys: name_suggestions "
        "(5 strings), positioning_statement (string), value_props (3 strings), "
        "elevator_pitch (string), color_palette_description (string), tone_guide "
        "(string). Do not include markdown."
    )
    user_prompt = (
        f"Concept: {concept}\n"
        f"Industry: {industry}\n"
        f"Style: {style}\n\n"
        "Create a differentiated brand strategy brief."
    )

    try:
        payload = generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model_id=os.getenv("MODEL_ID", "gpt-4o-mini"),
            max_tokens=1200,
        )
        return {
            "status": "success",
            "content": [{"text": json.dumps(payload, indent=2)}],
            "brand": payload,
        }
    except Exception as exc:
        message = f"Brand generation failed: {exc}"
        return {
            "status": "error",
            "content": [{"text": message}],
            "brand": {},
        }
