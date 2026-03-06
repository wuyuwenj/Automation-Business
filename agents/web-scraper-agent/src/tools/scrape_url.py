"""Single URL scraping using Apify website-content-crawler."""

import os
import httpx

APIFY_BASE = "https://api.apify.com/v2"


def scrape_url_impl(url: str, output_format: str = "markdown") -> dict:
    """Scrape a single URL and extract clean content.

    Args:
        url: The URL to scrape.
        output_format: "markdown" or "text" (default: "markdown").
    """
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        return {"status": "error", "content": [{"text": "APIFY_API_TOKEN not configured"}], "url": url, "title": "", "char_count": 0}

    try:
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(
                f"{APIFY_BASE}/acts/apify~website-content-crawler/run-sync-get-dataset-items",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "startUrls": [{"url": url}],
                    "maxCrawlDepth": 0,
                    "maxCrawlPages": 1,
                    "saveMarkdown": True,
                    "saveHtml": False,
                },
            )
            resp.raise_for_status()
            items = resp.json()

        if not items:
            return {"status": "error", "content": [{"text": f"No content extracted from {url}"}], "url": url, "title": "", "char_count": 0}

        item = items[0]
        title = item.get("title", "")
        content = item.get("markdown", "") if output_format == "markdown" else item.get("text", "")
        content = content[:4000] if content else ""

        text = f"## {title}\n\nSource: {url}\n\n{content}" if title else f"Source: {url}\n\n{content}"

        return {"status": "success", "content": [{"text": text}], "url": url, "title": title, "char_count": len(content)}

    except httpx.HTTPStatusError as exc:
        return {"status": "error", "content": [{"text": f"Apify API error: HTTP {exc.response.status_code}"}], "url": url, "title": "", "char_count": 0}
    except Exception as exc:
        return {"status": "error", "content": [{"text": f"Scraping failed: {exc}"}], "url": url, "title": "", "char_count": 0}
