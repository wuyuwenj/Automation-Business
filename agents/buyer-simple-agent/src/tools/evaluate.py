"""Post-purchase evaluation tool using a structured rubric.

After each purchase, the agent calls this to score the response
on 4 dimensions, calculate ROI, and record in the purchase ledger.
"""

from ..ledger import Evaluation, PurchaseLedger
from ..log import get_logger, log

_logger = get_logger("buyer.evaluate")


def evaluate_purchase_impl(
    ledger: PurchaseLedger,
    query: str,
    query_category: str,
    seller_url: str,
    seller_name: str,
    response_text: str,
    credits_spent: int,
    relevance: int,
    depth: int,
    actionability: int,
    specificity: int,
    reasoning: str,
) -> dict:
    """Evaluate a purchase response and record in the ledger.

    Args:
        ledger: PurchaseLedger instance.
        query: The original query sent to the seller.
        query_category: Category of the query (e.g. "research", "sentiment").
        seller_url: URL of the seller.
        seller_name: Name of the seller.
        response_text: The response received.
        credits_spent: Credits spent on this purchase.
        relevance: 0-2 score - did it answer the query?
        depth: 0-2 score - specific data/facts/numbers?
        actionability: 0-2 score - can user make a decision?
        specificity: 0-2 score - beyond generic/boilerplate?
        reasoning: Explanation of the scores.

    Returns:
        Dict with evaluation summary, ROI, and comparison with past purchases.
    """
    evaluation = Evaluation(
        relevance=min(relevance, 2),
        depth=min(depth, 2),
        actionability=min(actionability, 2),
        specificity=min(specificity, 2),
        reasoning=reasoning,
    )

    record = ledger.record(
        query=query,
        query_category=query_category,
        seller_url=seller_url,
        seller_name=seller_name,
        cost=credits_spent,
        response_summary=response_text[:300],
        evaluation=evaluation,
    )

    log(_logger, "EVALUATE", "SCORED",
        f"{seller_name}: quality={record.quality_score}/8 roi={record.roi:.1f} "
        f"[rel={relevance} dep={depth} act={actionability} spec={specificity}]")

    # Get comparison data
    seller_stats = ledger.get_seller_stats(seller_url)
    category_stats = ledger.get_category_stats(query_category)

    lines = [
        f"Evaluation recorded for purchase from {seller_name}:",
        f"  Quality: {record.quality_score}/8 "
        f"(relevance={relevance}, depth={depth}, actionability={actionability}, specificity={specificity})",
        f"  Cost: {credits_spent} credit(s)",
        f"  ROI: {record.roi:.1f} (quality per credit)",
        f"  Reasoning: {reasoning}",
        "",
        f"Seller lifetime stats ({seller_name}):",
        f"  Total purchases: {seller_stats.get('total_purchases', 0)}",
        f"  Avg quality: {seller_stats.get('avg_quality', 0)}/8",
        f"  Avg ROI: {seller_stats.get('avg_roi', 0):.1f}",
    ]

    if category_stats.get("total_purchases", 0) > 1:
        lines.append(f"\nCategory '{query_category}' comparison:")
        lines.append(f"  Sellers tried: {len(category_stats.get('sellers_tried', []))}")
        by_seller = category_stats.get("by_seller", {})
        for url, stats in by_seller.items():
            marker = " <-- BEST" if url == category_stats.get("best_seller", {}).get("url") else ""
            lines.append(
                f"  {stats['name']}: avg ROI {stats['avg_roi']:.1f} "
                f"({stats['purchases']} purchases){marker}"
            )

    log(_logger, "EVALUATE", "COMPARISON",
        f"category={query_category} sellers_tried={len(category_stats.get('sellers_tried', []))}")

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "purchase_id": record.id,
        "quality_score": record.quality_score,
        "roi": record.roi,
        "seller_stats": seller_stats,
        "category_stats": category_stats,
    }
