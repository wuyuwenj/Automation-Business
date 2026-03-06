"""Pricing tier definitions for the crypto market agent."""

PRICING_TIERS = {
    "simple": {
        "credits": 1,
        "description": "Real-time crypto price check",
        "tool": "price_check",
    },
    "medium": {
        "credits": 5,
        "description": "OHLCV market trend analysis",
        "tool": "market_analysis",
    },
    "complex": {
        "credits": 10,
        "description": "Full DeFi protocol report",
        "tool": "defi_report",
    },
}


def get_credits_for_complexity(complexity: str) -> int:
    tier = PRICING_TIERS.get(complexity, PRICING_TIERS["simple"])
    return tier["credits"]
