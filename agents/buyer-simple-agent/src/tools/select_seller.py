"""Seller selection tool using explore/exploit logic.

Checks purchase history, compares seller ROI, and decides whether to
use a proven seller or explore a new one. Logs reasoning explicitly
for hackathon judges to see.
"""

import random

from ..ledger import PurchaseLedger
from ..log import get_logger, log
from ..registry import SellerRegistry

_logger = get_logger("buyer.select")

# Probability of exploring a new seller even when an exploit choice exists
EXPLORE_PROBABILITY = 0.2


def select_seller_impl(
    query: str,
    query_category: str,
    seller_registry: SellerRegistry,
    ledger: PurchaseLedger,
    failed_sellers: set[str] | None = None,
) -> dict:
    """Select the best seller for a query using explore/exploit logic.

    Args:
        query: The user's query.
        query_category: Classified category (e.g. "research", "sentiment").
        seller_registry: Registry of available sellers.
        ledger: Purchase ledger with history.
        failed_sellers: Set of seller URLs that failed during this session.

    Returns:
        Dict with selected seller info and reasoning.
    """
    all_sellers = seller_registry.list_all()

    # Filter out sellers that failed during this session
    if failed_sellers:
        pre_filter = len(all_sellers)
        all_sellers = [s for s in all_sellers if s["url"] not in failed_sellers]
        if pre_filter != len(all_sellers):
            log(_logger, "SELECT", "FILTER",
                f"Skipped {pre_filter - len(all_sellers)} previously failed seller(s)")

    if not all_sellers:
        failed_count = len(failed_sellers) if failed_sellers else 0
        msg = "No sellers available."
        if failed_count:
            msg += f" ({failed_count} seller(s) were skipped due to previous failures.)"
        msg += " Use discover_marketplace first."
        return {
            "status": "error",
            "content": [{"text": msg}],
        }

    # Get which sellers we've tried for this category
    tried_urls = ledger.get_sellers_tried_for_category(query_category)
    category_stats = ledger.get_category_stats(query_category)

    # Separate tried vs untried sellers
    tried = [s for s in all_sellers if s["url"] in tried_urls]
    untried = [s for s in all_sellers if s["url"] not in tried_urls]

    decision = ""
    selected = None

    if not tried_urls:
        # EXPLORE: Never bought in this category before
        # Pick cheapest relevant seller to minimize exploration cost
        candidates = sorted(all_sellers, key=lambda s: s["credits"])
        selected = candidates[0]
        decision = (
            f"EXPLORE: No purchase history for category '{query_category}'. "
            f"Starting with cheapest seller '{selected['name']}' "
            f"({selected['credits']} credit(s)) to minimize exploration cost."
        )

    elif len(tried_urls) < 2:
        # EXPLORE: Only tried one seller, need comparison
        if untried:
            # Pick cheapest untried seller
            candidates = sorted(untried, key=lambda s: s["credits"])
            selected = candidates[0]
            decision = (
                f"EXPLORE: Only tried 1 seller for '{query_category}'. "
                f"Trying '{selected['name']}' for comparison. "
                f"Need at least 2 sellers to make an informed decision."
            )
        else:
            # All sellers tried, exploit the best
            best_url = category_stats.get("best_seller", {}).get("url")
            selected = next((s for s in all_sellers if s["url"] == best_url), all_sellers[0])
            decision = (
                f"EXPLOIT: All available sellers already tried for '{query_category}'. "
                f"Selecting best performer '{selected['name']}' "
                f"(avg ROI: {category_stats['best_seller'].get('avg_roi', '?')})."
            )

    else:
        # 2+ sellers tried: EXPLOIT (80%) or EXPLORE (20%)
        should_explore = untried and random.random() < EXPLORE_PROBABILITY

        if should_explore:
            candidates = sorted(untried, key=lambda s: s["credits"])
            selected = candidates[0]
            decision = (
                f"EXPLORE (periodic re-evaluation): Already tried {len(tried_urls)} sellers "
                f"for '{query_category}', but checking if '{selected['name']}' "
                f"offers better value."
            )
        else:
            # EXPLOIT: pick highest ROI seller
            best_url = category_stats.get("best_seller", {}).get("url")
            best_stats = category_stats.get("by_seller", {}).get(best_url, {})
            selected = next((s for s in all_sellers if s["url"] == best_url), tried[0] if tried else all_sellers[0])

            # Build comparison string
            comparison_parts = []
            for url, stats in category_stats.get("by_seller", {}).items():
                marker = " (BEST)" if url == best_url else ""
                comparison_parts.append(
                    f"{stats['name']}: avg ROI {stats['avg_roi']:.1f} "
                    f"over {stats['purchases']} purchase(s){marker}"
                )

            decision = (
                f"EXPLOIT: Selecting '{selected['name']}' — best ROI for '{query_category}'. "
                f"Comparison: {' | '.join(comparison_parts)}. "
                f"Reason: Higher quality per credit based on {best_stats.get('purchases', 0)} "
                f"previous purchase(s)."
            )

    log(_logger, "SELECT", "DECISION", decision)

    lines = [
        f"Seller selection for category '{query_category}':",
        f"  Selected: {selected['name']} ({selected['url']})",
        f"  Cost: {selected['cost_description'] or str(selected['credits']) + ' credit(s)'}",
        f"  Decision: {decision}",
        "",
        f"  Available sellers: {len(all_sellers)}",
        f"  Tried for this category: {len(tried_urls)}",
        f"  Untried: {len(untried)}",
    ]

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "selected_seller": {
            "name": selected["name"],
            "url": selected["url"],
            "credits": selected["credits"],
            "cost_description": selected["cost_description"],
        },
        "decision": decision,
        "phase": "explore" if "EXPLORE" in decision else "exploit",
    }
