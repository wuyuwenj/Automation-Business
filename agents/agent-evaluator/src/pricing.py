"""Pricing tier definitions for the agent evaluator."""

PRICING_TIERS = {
    "simple": {"credits": 1, "description": "Single agent health check", "tool": "check_agent"},
    "medium": {"credits": 3, "description": "Discover available agents", "tool": "discover_agents"},
    "complex": {"credits": 10, "description": "Full agent evaluation report", "tool": "evaluate_agents"},
}

def get_credits_for_complexity(complexity: str) -> int:
    tier = PRICING_TIERS.get(complexity, PRICING_TIERS["simple"])
    return tier["credits"]
