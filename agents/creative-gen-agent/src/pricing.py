"""Pricing tiers for the creative generation selling agent."""

PRICING_TIERS = {
    "copy": {
        "credits": 1,
        "description": "Ad copy bundle with headlines, taglines, CTAs, and social posts",
        "tool": "generate_copy",
    },
    "brand": {
        "credits": 5,
        "description": "Brand strategy brief with naming, positioning, and tone guidance",
        "tool": "generate_brand",
    },
    "landing": {
        "credits": 10,
        "description": "Single-file landing page HTML ready to preview or save",
        "tool": "generate_landing_page",
    },
}


def get_credits_for_complexity(complexity: str) -> int:
    """Return the credit cost for a given tier."""
    tier = PRICING_TIERS.get(complexity, PRICING_TIERS["copy"])
    return tier["credits"]
