"""Landing page generation tool backed by Gemini generation."""

import os
from pathlib import Path

import httpx

from ..gemini_utils import generate_json
from ..storage import maybe_upload_html, save_html_locally


def generate_landing_page_impl(
    product_name: str,
    description: str,
    features: str = "",
    cta_text: str = "Get Started",
) -> dict:
    """Generate a complete single-file landing page."""
    system_instruction = (
        "You are an expert landing page strategist, copywriter, and front-end designer. "
        "Return valid JSON only with exactly these keys: html (string), summary (string), "
        "suggested_filename (string). The html value must be a complete HTML document "
        "starting with <!DOCTYPE html>, using the Tailwind CSS CDN, responsive on mobile "
        "and desktop, and ready to save as a file. Do not include markdown fences."
    )
    user_prompt = (
        f"Product name: {product_name}\n"
        f"Description: {description}\n"
        f"Features: {features or 'None provided'}\n"
        f"CTA text: {cta_text}\n\n"
        "Create a polished single-file landing page."
    )
    response_schema = {
        "type": "object",
        "properties": {
            "html": {"type": "string"},
            "summary": {"type": "string"},
            "suggested_filename": {"type": "string"},
        },
        "required": ["html", "summary", "suggested_filename"],
    }

    try:
        primary_model = os.getenv("GEMINI_MODEL_ID", "gemini-2.5-pro")
        fallback_model = os.getenv("GEMINI_FALLBACK_MODEL_ID", "gemini-2.5-flash")
        try:
            payload = generate_json(
                system_instruction=system_instruction,
                user_prompt=user_prompt,
                model_id=primary_model,
                response_schema=response_schema,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or fallback_model == primary_model:
                raise
            payload = generate_json(
                system_instruction=system_instruction,
                user_prompt=user_prompt,
                model_id=fallback_model,
                response_schema=response_schema,
            )
        html = payload.get("html", "")
        summary = payload.get("summary", "")
        suggested_filename = payload.get("suggested_filename", "landing-page.html")
        if not suggested_filename.endswith(".html"):
            suggested_filename = f"{Path(suggested_filename).stem or 'landing-page'}.html"
        local_path = save_html_locally(html, suggested_filename)
        upload_data = maybe_upload_html(
            html=html,
            product_name=product_name,
            suggested_filename=suggested_filename,
        )
        return {
            "status": "success",
            "content": [{"text": summary or "Landing page generated"}],
            "html": html,
            "summary": summary,
            "suggested_filename": suggested_filename,
            "saved_path": local_path,
            "preview_url": upload_data.get("preview_url", ""),
            "download_url": upload_data.get("download_url", ""),
            "storage": upload_data.get("storage", "local"),
        }
    except Exception as exc:
        message = f"Landing page generation failed: {exc}"
        return {
            "status": "error",
            "content": [{"text": message}],
            "html": "",
            "summary": "",
            "suggested_filename": "landing-page.html",
            "saved_path": "",
            "preview_url": "",
            "download_url": "",
            "storage": "",
        }
