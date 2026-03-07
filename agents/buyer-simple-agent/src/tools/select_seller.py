"""Seller selection tool using explore/exploit logic.

Uses category-level comparison memory to track which sellers have been
tested. Picks untested sellers first (explore), then reuses the best
performer once 2+ sellers are tested (exploit).
"""

from openai import OpenAI

from ..comparison_memory import CategoryComparisonMemory
from ..ledger import PurchaseLedger
from ..log import get_logger, log
from ..registry import SellerRegistry
from .filter_sellers import rank_sellers_for_query

_logger = get_logger("buyer.select")

# Marketplace categories from Nevermined Discovery API + common task types
_KNOWN_CATEGORIES = [
    "DeFi", "Data Analytics", "AI/ML", "RegTech", "IoT", "Security",
    "Infrastructure", "Identity", "Social", "Gaming", "API Services",
    "Agent Match Maker", "Agent Review Board", "Banking, Capital",
    "Business Intelligence", "Business Operations", "Dynamic pricing",
    "Fast cleaning service", "Grocery", "Marketing & Advertising",
    "Premium cleaning services", "Quality Assurance", "Research",
    "Research & Analysis", "Trust & Compliance", "memory",
    "Web Scraping",
]


# Normalize category names so old and new data stay consistent
_CATEGORY_ALIASES = {
    "web scraping": "scraping",
}


def _classify_query(query: str, client: OpenAI | None) -> str:
    """Classify a query into one marketplace category using LLM."""
    if not client:
        return ""
    categories_str = ", ".join(_KNOWN_CATEGORIES)
    try:
        resp = client.chat.completions.create(
            model="openai/gpt-4.1-nano",
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this query into exactly ONE category from the list below. "
                    f"Reply with ONLY the category name, nothing else.\n\n"
                    f"Categories: {categories_str}\n\n"
                    f"Query: {query}"
                ),
            }],
            max_tokens=20,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip().strip('"').strip("'")
        # Match against known categories (case-insensitive)
        for cat in _KNOWN_CATEGORIES:
            if raw.lower() == cat.lower():
                result = _CATEGORY_ALIASES.get(cat.lower(), cat.lower())
                log(_logger, "SELECT", "CLASSIFY", f"'{query[:50]}' → {result}")
                return result
        # Partial match fallback
        for cat in _KNOWN_CATEGORIES:
            if cat.lower() in raw.lower() or raw.lower() in cat.lower():
                result = _CATEGORY_ALIASES.get(cat.lower(), cat.lower())
                log(_logger, "SELECT", "CLASSIFY", f"'{query[:50]}' → {result} (fuzzy)")
                return result
        log(_logger, "SELECT", "CLASSIFY", f"no match for '{raw}', using raw")
        result = raw.lower()
        return _CATEGORY_ALIASES.get(result, result)
    except Exception as e:
        log(_logger, "SELECT", "CLASSIFY", f"failed: {e}")
        return ""


def _sort_candidates(sellers: list[dict]) -> list[dict]:
    """Sort sellers: agent_id resolved first, then free-plan, then cheapest."""
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
    comparison_memory: CategoryComparisonMemory | None = None,
    failed_sellers: set[str] | None = None,
    embedding_client: OpenAI | None = None,
) -> dict:
    """Select the best seller for a query using explore/exploit logic.

    Args:
        query: The user's query.
        query_category: Classified category (e.g. "research", "sentiment").
        seller_registry: Registry of available sellers.
        ledger: Purchase ledger with history.
        comparison_memory: Category-level comparison state.
        failed_sellers: Set of seller URLs that failed during this session.
        embedding_client: OpenAI client for embedding-based ranking.
    """
    # Auto-classify query into a marketplace category
    classified = _classify_query(query, embedding_client)
    if classified:
        query_category = classified

    all_sellers = seller_registry.list_all(verbose=True)

    # Filter out blocked sellers (persistent blocklist)
    blocked_count = 0
    if comparison_memory:
        blocked_urls = comparison_memory.get_blocked_urls()
        if blocked_urls:
            pre_block = len(all_sellers)
            all_sellers = [s for s in all_sellers if s["url"] not in blocked_urls]
            blocked_count = pre_block - len(all_sellers)
            if blocked_count:
                log(_logger, "SELECT", "FILTER",
                    f"Skipped {blocked_count} blocked seller(s)")

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
        return {"status": "error", "content": [{"text": msg}]}

    # Rank candidates using embeddings (or keyword fallback)
    ranked_candidates = rank_sellers_for_query(
        query,
        sellers=all_sellers,
        max_results=10,
        embedding_client=embedding_client,
    )
    relevant_candidates = [
        s for s in ranked_candidates if s.get("relevance_score", 0) > 0
    ]
    if not relevant_candidates:
        relevant_candidates = _sort_candidates(all_sellers)

    seller_map = {s["url"]: s for s in all_sellers}
    decision = ""
    selected = None

    # -- Comparison memory path: always explore untested sellers first --------
    if comparison_memory:
        # Pick the next untested seller — try relevant first, then all
        next_seller = comparison_memory.select_next_seller(
            query_category, relevant_candidates, failed_sellers,
        )
        if not next_seller:
            # Relevant candidates exhausted — try ALL sellers before exploiting
            next_seller = comparison_memory.select_next_seller(
                query_category, _sort_candidates(all_sellers), failed_sellers,
            )
        if next_seller:
            record = comparison_memory.get_or_create(query_category)
            tested_count = len(record.tested_urls())
            total = len(all_sellers)
            free_tag = " (FREE plan)" if next_seller.get("has_free_plan") else ""
            decision = (
                f"EXPLORE: Testing '{next_seller['name']}'{free_tag} for category "
                f"'{query_category}'. {tested_count}/{total} seller(s) tested so far."
            )
            log(_logger, "SELECT", "DECISION", decision)
            return _build_result(next_seller, decision, "compare", query_category, record)

        # Every seller tested — exploit the best
        record = comparison_memory.get_or_create(query_category)
        tested_count = len([s for s in record.tested_sellers if s.attempts > 0])
        preferred_url = record.preferred_seller_url
        if preferred_url:
            preferred = seller_map.get(preferred_url)
            if preferred:
                decision = (
                    f"EXPLOIT: All {tested_count} sellers tested for '{query_category}'. "
                    f"Reusing best performer '{preferred['name']}'."
                )
                log(_logger, "SELECT", "DECISION", decision)
                return _build_result(preferred, decision, "exploit", query_category, record)

    # -- Fallback: ledger-based explore/exploit ------------------------------
    tried_urls = ledger.get_sellers_tried_for_category(query_category)

    if not tried_urls:
        candidates = _sort_candidates(all_sellers)
        selected = candidates[0]
        free_tag = " (FREE plan)" if selected.get("has_free_plan") else ""
        decision = (
            f"EXPLORE: No purchase history for category '{query_category}'. "
            f"Starting with '{selected['name']}'{free_tag}."
        )
    else:
        best_url = ledger.get_best_seller_url()
        selected = next(
            (s for s in all_sellers if s["url"] == best_url),
            all_sellers[0],
        )
        decision = (
            f"EXPLOIT (ledger fallback): Selecting '{selected['name']}' "
            f"as best known performer."
        )

    log(_logger, "SELECT", "DECISION", decision)
    return {
        "status": "success",
        "content": [{"text": "\n".join([
            f"Seller selection for category '{query_category}':",
            f"  Selected: {selected['name']} ({selected['url']})",
            f"  Cost: {selected['cost_description'] or str(selected['credits']) + ' credit(s)'}",
            f"  Decision: {decision}",
            f"  Available: {len(all_sellers)} | Failed: {failed_count}",
        ])}],
        "selected_seller": {
            "name": selected["name"],
            "url": selected["url"],
            "credits": selected["credits"],
            "cost_description": selected["cost_description"],
        },
        "decision": decision,
        "phase": "explore" if "EXPLORE" in decision else "exploit",
    }


def _build_result(
    seller: dict,
    decision: str,
    phase: str,
    query_category: str,
    record=None,
) -> dict:
    """Build a standardized selection result."""
    lines = [
        f"Seller selection for category '{query_category}':",
        f"  Selected: {seller['name']} ({seller['url']})",
        f"  Cost: {seller.get('cost_description') or str(seller.get('credits', 1)) + ' credit(s)'}",
        f"  Decision: {decision}",
    ]
    if record:
        tested_count = len([s for s in record.tested_sellers if s.attempts > 0])
        lines.append(f"  Sellers tested in category: {tested_count}")
        if record.preferred_seller_url:
            pref = record.get_result(record.preferred_seller_url)
            if pref:
                lines.append(
                    f"  Current best: {pref.seller_name} "
                    f"(score={pref.quality_score:.1f})"
                )

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "selected_seller": {
            "name": seller["name"],
            "url": seller["url"],
            "credits": seller.get("credits", 1),
            "cost_description": seller.get("cost_description", ""),
        },
        "decision": decision,
        "phase": phase,
    }
