"""Batch scraping of multiple URLs using Apify."""

import os
import httpx

APIFY_BASE = "https://api.apify.com/v2"


def batch_scrape_impl(urls: str) -> dict:
    """Scrape up to 5 URLs and return structured content.

    Args:
        urls: Comma-separated URLs to scrape (max 5).
    """
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        return {"status": "error", "content": [{"text": "APIFY_API_TOKEN not configured"}], "results": [], "total_pages": 0}

    url_list = [u.strip() for u in urls.split(",") if u.strip()][:5]
    if not url_list:
        return {"status": "error", "content": [{"text": "No valid URLs provided"}], "results": [], "total_pages": 0}

    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(
                f"{APIFY_BASE}/acts/apify~website-content-crawler/run-sync-get-dataset-items",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "startUrls": [{"url": u} for u in url_list],
                    "maxCrawlDepth": 0,
                    "maxCrawlPages": len(url_list),
                    "saveMarkdown": True,
                    "saveHtml": False,
                },
            )
            resp.raise_for_status()
            items = resp.json()

        results = []
        text_parts = []
        for item in items:
            page_url = item.get("url", "")
            title = item.get("title", "")
            markdown = item.get("markdown", "") or item.get("text", "")
            excerpt = markdown[:500] if markdown else ""

            results.append({"url": page_url, "title": title, "excerpt": excerpt})
            text_parts.append(f"### {title or page_url}\nURL: {page_url}\n\n{excerpt}\n")

        combined_text = f"## Batch Scrape Results ({len(results)} pages)\n\n" + "\n---\n".join(text_parts)

        return {"status": "success", "content": [{"text": combined_text}], "results": results, "total_pages": len(results)}

    except httpx.HTTPStatusError as exc:
        return {"status": "error", "content": [{"text": f"Apify API error: HTTP {exc.response.status_code}"}], "results": [], "total_pages": 0}
    except Exception as exc:
        return {"status": "error", "content": [{"text": f"Batch scrape failed: {exc}"}], "results": [], "total_pages": 0}
