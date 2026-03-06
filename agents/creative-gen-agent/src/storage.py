"""Storage helpers for generated landing page previews."""

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts) or "landing-page"


def save_html_locally(html: str, suggested_filename: str) -> str:
    """Save generated HTML under tmp/generated-pages and return the absolute path."""
    output_dir = Path(__file__).resolve().parents[1] / "tmp" / "generated-pages"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / suggested_filename
    path.write_text(html, encoding="utf-8")
    return str(path.resolve())


def maybe_upload_html(
    *,
    html: str,
    product_name: str,
    suggested_filename: str,
) -> dict[str, Any]:
    """Upload generated HTML to S3 if configured and return preview metadata."""
    bucket = os.environ.get("LANDING_PAGE_S3_BUCKET", "").strip()
    if not bucket:
        return {}

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    prefix = os.environ.get("LANDING_PAGE_S3_PREFIX", "landing-pages").strip("/") or "landing-pages"
    public_base_url = os.environ.get("LANDING_PAGE_PUBLIC_BASE_URL", "").rstrip("/")
    expires_in = int(os.environ.get("LANDING_PAGE_URL_EXPIRES_SECONDS", "86400"))

    product_slug = _slugify(product_name)
    object_key = f"{prefix}/{product_slug}-{uuid4().hex[:8]}.html"

    s3 = boto3.client("s3", region_name=region)
    s3.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=html.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        CacheControl="public, max-age=300",
    )

    if public_base_url:
        preview_url = f"{public_base_url}/{object_key}"
        download_url = preview_url
    else:
        preview_url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": object_key,
                "ResponseContentType": "text/html; charset=utf-8",
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=expires_in,
        )
        download_url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": object_key,
                "ResponseContentType": "text/html; charset=utf-8",
                "ResponseContentDisposition": f'attachment; filename="{suggested_filename}"',
            },
            ExpiresIn=expires_in,
        )

    return {
        "storage": "s3",
        "bucket": bucket,
        "object_key": object_key,
        "preview_url": preview_url,
        "download_url": download_url,
    }
