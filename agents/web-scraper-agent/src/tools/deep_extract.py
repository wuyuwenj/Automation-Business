"""Deep site extraction using Apify + OpenAI synthesis."""

import os
import httpx
from openai import OpenAI

APIFY_BASE = "https://api.apify.com/v2"


def deep_extract_impl(url: str, max_pages: int = 5) -> dict:
    """Deep crawl a site and produce an LLM-synthesized summary.

    Args:
        url: Starting URL to deep-crawl.
        max_pages: Max pages to crawl (default: 5, max: 10).
    """
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        return {"status": "error", "content": [{"text": "APIFY_API_TOKEN not configured"}], "report": "", "pages_scraped": 0, "urls": []}

    max_pages = min(max_pages, 10)

    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(
                f"{APIFY_BASE}/acts/apify~website-content-crawler/run-sync-get-dataset-items",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "startUrls": [{"url": url}],
                    "maxCrawlDepth": 1,
                    "maxCrawlPages": max_pages,
                    "saveMarkdown": True,
                    "saveHtml": False,
                },
            )
            resp.raise_for_status()
            items = resp.json()

        if not items:
            return {"status": "error", "content": [{"text": f"No content extracted from {url}"}], "report": "", "pages_scraped": 0, "urls": []}

        # Combine content from all pages
        urls_found = []
        combined_content = ""
        for item in items:
            page_url = item.get("url", "")
            title = item.get("title", "")
            markdown = item.get("markdown", "") or item.get("text", "")
            urls_found.append(page_url)
            combined_content += f"\n\n--- Page: {title} ({page_url}) ---\n{markdown[:1500]}"

        combined_content = combined_content[:6000]

        # Send to OpenAI for synthesis
        openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        completion = openai_client.chat.completions.create(
            model=os.environ.get("MODEL_ID", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a web content analyst. Based on the scraped content from "
                        "multiple pages of a website, write a comprehensive summary including:\n"
                        "1. Site Overview\n"
                        "2. Key Content Topics\n"
                        "3. Important Data Points\n"
                        "4. Notable Links/References\n"
                        "Keep the summary under 500 words."
                    ),
                },
                {"role": "user", "content": f"Starting URL: {url}\n\nPages scraped: {len(items)}\n\n{combined_content}"},
            ],
            max_tokens=800,
        )
        report = completion.choices[0].message.content

        return {"status": "success", "content": [{"text": report}], "report": report, "pages_scraped": len(items), "urls": urls_found}

    except httpx.HTTPStatusError as exc:
        return {"status": "error", "content": [{"text": f"Apify API error: HTTP {exc.response.status_code}"}], "report": "", "pages_scraped": 0, "urls": []}
    except Exception as exc:
        return {"status": "error", "content": [{"text": f"Deep extract failed: {exc}"}], "report": "", "pages_scraped": 0, "urls": []}
