"""Pricing tier definitions for the web scraper agent."""

PRICING_TIERS = {
    "simple": {"credits": 1, "description": "Single URL scrape", "tool": "scrape_url"},
    "medium": {"credits": 5, "description": "Batch scrape up to 5 URLs", "tool": "batch_scrape"},
    "complex": {"credits": 10, "description": "Deep site extraction with LLM summary", "tool": "deep_extract"},
}

def get_credits_for_complexity(complexity: str) -> int:
    tier = PRICING_TIERS.get(complexity, PRICING_TIERS["simple"])
    return tier["credits"]
