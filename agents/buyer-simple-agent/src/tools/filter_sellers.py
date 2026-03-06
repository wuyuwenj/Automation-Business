"""Pre-purchase seller filtering using free metadata.

Matches sellers to a query using keywords, description, and category
without spending any credits. Returns ranked relevant sellers.
"""

from ..log import get_logger, log
from ..registry import SellerRegistry

_logger = get_logger("buyer.filter")


def _score_sellers(query: str, sellers: list[dict]) -> list[dict]:
    """Attach relevance scores and reasons to seller metadata."""
    query_lower = query.lower()
    query_words = set(query_lower.split())

    scored = []
    for seller in sellers:
        score = 0
        reasons = []

        # Keyword match (each matching keyword = 2 points)
        seller_keywords = [kw.lower() for kw in seller.get("keywords", [])]
        matching_keywords = [kw for kw in seller_keywords if kw in query_lower]
        if matching_keywords:
            score += len(matching_keywords) * 2
            reasons.append(f"keywords: {', '.join(matching_keywords)}")

        # Category match (3 points for exact, 1 for partial)
        category = seller.get("category", "").lower()
        if category:
            if category in query_lower or any(w in category for w in query_words):
                score += 3
                reasons.append(f"category: {seller.get('category')}")
            elif any(kw in category for kw in seller_keywords[:3]):
                score += 1

        # Description word overlap (1 point per matching word, max 5)
        desc_words = set(seller.get("description", "").lower().split())
        overlap = query_words & desc_words
        overlap_count = min(len(overlap), 5)
        if overlap_count > 0:
            score += overlap_count
            reasons.append(f"description overlap: {overlap_count} words")

        # Skill match (2 points per matching skill)
        skills = [s.lower() for s in seller.get("skills", [])]
        matching_skills = [s for s in skills if s in query_lower]
        if matching_skills:
            score += len(matching_skills) * 2
            reasons.append(f"skills: {', '.join(matching_skills)}")

        scored.append({
            **seller,
            "relevance_score": score,
            "match_reasons": reasons,
        })

    scored.sort(key=lambda s: s["relevance_score"], reverse=True)
    return scored


def rank_sellers_for_query(
    query: str,
    seller_registry: SellerRegistry | None = None,
    sellers: list[dict] | None = None,
    max_results: int = 3,
) -> list[dict]:
    """Rank sellers for a query using free metadata only."""
    seller_rows = sellers if sellers is not None else (
        seller_registry.list_all(verbose=True) if seller_registry else []
    )
    if not seller_rows:
        return []
    scored = _score_sellers(query, seller_rows)
    return scored[:max_results]


def filter_sellers_impl(
    query: str,
    seller_registry: SellerRegistry,
) -> dict:
    """Find the most relevant sellers for a query using free metadata."""
    top = rank_sellers_for_query(query, seller_registry=seller_registry, max_results=3)
    if not top:
        return {
            "status": "error",
            "content": [{"text": "No sellers available. Use discover_marketplace first."}],
        }

    log(_logger, "FILTER", "MATCHED",
        f"query='{query[:50]}' top_match={top[0]['name'] if top else 'none'} "
        f"score={top[0]['relevance_score'] if top else 0}")

    lines = [f"Top {len(top)} matches for \"{query[:40]}\":"]
    for i, s in enumerate(top):
        lines.append(f"  {i+1}. {s['name']} score={s['relevance_score']} {s['credits']}cr ({s['url']})")

    if not any(s["relevance_score"] > 0 for s in top):
        lines.append("  No strong matches. Broaden query.")

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "ranked_sellers": [
            {
                "name": s["name"],
                "url": s["url"],
                "relevance_score": s["relevance_score"],
                "match_reasons": s["match_reasons"],
                "credits": s["credits"],
                "cost_description": s["cost_description"],
            }
            for s in top
        ],
    }
