"""Seller selection tool using explore/exploit logic.

Checks purchase history, compares seller ROI, and decides whether to
use a proven seller or explore a new one. Logs reasoning explicitly
for hackathon judges to see.
"""

import random

from ..comparison_memory import TaskComparisonMemory
from ..ledger import PurchaseLedger
from ..log import get_logger, log
from ..registry import SellerRegistry
from .filter_sellers import rank_sellers_for_query

_logger = get_logger("buyer.select")

# Probability of exploring a new seller even when an exploit choice exists
EXPLORE_PROBABILITY = 0.2

# Maximum failed explores before falling back to a known-good seller
MAX_FAILED_EXPLORES = 2


def _sort_candidates(sellers: list[dict]) -> list[dict]:
    """Sort sellers: agent_id resolved first, then free-plan, then by credits ascending.

    Sellers without a resolved agent_id are very likely to fail (403) because
    their Discovery API plan IDs don't match the sandbox environment.
    """
    return sorted(sellers, key=lambda s: (
        0 if s.get("has_agent_id") else 1,
        0 if s.get("has_free_plan") else 1,
        s["credits"],
    ))


def select_seller_impl(
    query: str,
    query_category: str,
    seller_registry: SellerRegistry,
    ledger: PurchaseLedger,
    comparison_memory: TaskComparisonMemory | None = None,
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
    all_sellers = seller_registry.list_all(verbose=True)

    # Filter out sellers that failed during this session
    failed_count = 0
    if failed_sellers:
        pre_filter = len(all_sellers)
        all_sellers = [s for s in all_sellers if s["url"] not in failed_sellers]
        failed_count = pre_filter - len(all_sellers)
        if failed_count:
            log(_logger, "SELECT", "FILTER",
                f"Skipped {failed_count} previously failed seller(s)")

    if not all_sellers:
        msg = "No sellers available."
        if failed_count:
            msg += f" ({failed_count} seller(s) were skipped due to previous failures.)"
        msg += " Use discover_marketplace first."
        return {
            "status": "error",
            "content": [{"text": msg}],
        }

    seller_map = {seller["url"]: seller for seller in all_sellers}
    ranked_candidates = rank_sellers_for_query(
        query,
        sellers=all_sellers,
        max_results=max(5, len(all_sellers)),
    )
    relevant_candidates = [
        seller for seller in ranked_candidates
        if seller.get("relevance_score", 0) > 0
    ]
    if not relevant_candidates:
        relevant_candidates = _sort_candidates(all_sellers)

    if comparison_memory:
        comparison = comparison_memory.get_for_query(query, query_category)
        if comparison and comparison.needs_rebrowse:
            previous_urls = {
                slot.seller_url for slot in comparison.sellers() if slot.seller_url
            }
            if len(relevant_candidates) > len(previous_urls):
                comparison = comparison_memory.ensure_pair(
                    query=query,
                    query_category=query_category,
                    candidate_sellers=relevant_candidates,
                    exclude_urls=previous_urls,
                    force_replace=True,
                )
        else:
            comparison = comparison_memory.ensure_pair(
                query=query,
                query_category=query_category,
                candidate_sellers=relevant_candidates,
            )

        if comparison:
            pair_slots = [comparison.seller_a, comparison.seller_b]
            pair_sellers = [
                seller_map[slot.seller_url]
                for slot in pair_slots
                if slot.seller_url in seller_map
            ]
            if pair_sellers:
                untested_slots = [
                    slot for slot in pair_slots
                    if slot.seller_url and not slot.tested and slot.seller_url in seller_map
                ]
                if untested_slots:
                    slot = untested_slots[0]
                    selected = seller_map[slot.seller_url]
                    tested_count = sum(1 for pair_slot in pair_slots if pair_slot.tested)
                    decision = (
                        f"TASK_COMPARE: task pair for '{query_category}' is "
                        f"'{comparison.seller_a.seller_name or comparison.seller_a.seller_url}' vs "
                        f"'{comparison.seller_b.seller_name or comparison.seller_b.seller_url or 'pending'}'. "
                        f"Selecting '{selected['name']}' as comparison step {tested_count + 1}/2."
                    )
                    log(_logger, "SELECT", "DECISION", decision)
                    return {
                        "status": "success",
                        "content": [{"text": "\n".join([
                            f"Seller selection for category '{query_category}':",
                            f"  Task key: {comparison.task_key}",
                            f"  Selected: {selected['name']} ({selected['url']})",
                            f"  Cost: {selected['cost_description'] or str(selected['credits']) + ' credit(s)'}",
                            f"  Decision: {decision}",
                            f"  Pair: {comparison.seller_a.seller_name or comparison.seller_a.seller_url} vs "
                            f"{comparison.seller_b.seller_name or comparison.seller_b.seller_url or 'pending'}",
                        ])}],
                        "selected_seller": {
                            "name": selected["name"],
                            "url": selected["url"],
                            "credits": selected["credits"],
                            "cost_description": selected["cost_description"],
                        },
                        "decision": decision,
                        "phase": "compare",
                        "task_key": comparison.task_key,
                    }

                preferred_url = comparison.preferred_seller_url
                preferred = seller_map.get(preferred_url, None) if preferred_url else None
                if preferred and not comparison.needs_rebrowse:
                    decision = (
                        f"TASK_EXPLOIT: Reusing preferred seller '{preferred['name']}' for task "
                        f"'{comparison.task_key}'. Stored pair scores are above the minimum "
                        f"acceptable score ({comparison.minimum_acceptable_score:.1f})."
                    )
                    log(_logger, "SELECT", "DECISION", decision)
                    return {
                        "status": "success",
                        "content": [{"text": "\n".join([
                            f"Seller selection for category '{query_category}':",
                            f"  Task key: {comparison.task_key}",
                            f"  Selected: {preferred['name']} ({preferred['url']})",
                            f"  Cost: {preferred['cost_description'] or str(preferred['credits']) + ' credit(s)'}",
                            f"  Decision: {decision}",
                            f"  Pair scores: "
                            f"{comparison.seller_a.seller_name or comparison.seller_a.seller_url}="
                            f"{comparison.seller_a.quality_score:.1f}, "
                            f"{comparison.seller_b.seller_name or comparison.seller_b.seller_url}="
                            f"{comparison.seller_b.quality_score:.1f}",
                        ])}],
                        "selected_seller": {
                            "name": preferred["name"],
                            "url": preferred["url"],
                            "credits": preferred["credits"],
                            "cost_description": preferred["cost_description"],
                        },
                        "decision": decision,
                        "phase": "exploit",
                        "task_key": comparison.task_key,
                    }

    # Get which sellers we've tried for this category
    tried_urls = ledger.get_sellers_tried_for_category(query_category)
    category_stats = ledger.get_category_stats(query_category)

    # Separate tried vs untried sellers
    tried = [s for s in all_sellers if s["url"] in tried_urls]
    untried = [s for s in all_sellers if s["url"] not in tried_urls]

    # If too many failed explores this session, skip exploration and use known-good
    # Look for known-good sellers from ANY category (not just current)
    best_global_url = ledger.get_best_seller_url()
    has_known_good = best_global_url and any(s["url"] == best_global_url for s in all_sellers)
    should_force_exploit = failed_count >= MAX_FAILED_EXPLORES and (tried or has_known_good)

    decision = ""
    selected = None

    if should_force_exploit:
        # Too many failures — fall back to best known-good seller
        # Try category-specific best first, then global best
        best_url = category_stats.get("best_seller", {}).get("url") or best_global_url
        selected = next(
            (s for s in all_sellers if s["url"] == best_url),
            tried[0] if tried else all_sellers[0],
        )
        source = "this category" if best_url != best_global_url else "all categories"
        decision = (
            f"EXPLOIT (failsafe): {failed_count} seller(s) failed this session. "
            f"Falling back to best seller '{selected['name']}' (from {source}) "
            f"instead of continuing to explore."
        )

    elif not tried_urls:
        # EXPLORE: Never bought in this category before
        # Pick free-plan seller first, then cheapest
        candidates = _sort_candidates(all_sellers)
        selected = candidates[0]
        free_tag = " (FREE plan)" if selected.get("has_free_plan") else ""
        decision = (
            f"EXPLORE: No purchase history for category '{query_category}'. "
            f"Starting with '{selected['name']}'{free_tag} "
            f"({selected['credits']} credit(s)) to minimize exploration cost."
        )

    elif len(tried_urls) < 2:
        # EXPLORE: Only tried one seller, need comparison
        if untried:
            candidates = _sort_candidates(untried)
            selected = candidates[0]
            free_tag = " (FREE plan)" if selected.get("has_free_plan") else ""
            decision = (
                f"EXPLORE: Only tried 1 seller for '{query_category}'. "
                f"Trying '{selected['name']}'{free_tag} for comparison. "
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
            candidates = _sort_candidates(untried)
            selected = candidates[0]
            free_tag = " (FREE plan)" if selected.get("has_free_plan") else ""
            decision = (
                f"EXPLORE (periodic re-evaluation): Already tried {len(tried_urls)} sellers "
                f"for '{query_category}', but checking if '{selected['name']}'{free_tag} "
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
        f"  Free plan: {'yes' if selected.get('has_free_plan') else 'no'}",
        f"  Decision: {decision}",
        "",
        f"  Available sellers: {len(all_sellers)}",
        f"  Tried for this category: {len(tried_urls)}",
        f"  Untried: {len(untried)}",
        f"  Failed this session: {failed_count}",
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
