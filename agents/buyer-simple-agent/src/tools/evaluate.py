"""Post-purchase evaluation tool using a structured rubric.

After each purchase, the agent calls this to score the response
on 4 dimensions, calculate ROI, and record in the purchase ledger.
"""

from ..comparison_memory import CategoryComparisonMemory
from ..ledger import Evaluation, PurchaseLedger
from ..log import get_logger, log

_logger = get_logger("buyer.evaluate")


def evaluate_purchase_impl(
    ledger: PurchaseLedger,
    comparison_memory: CategoryComparisonMemory | None,
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
        comparison_memory: Category-level comparison memory.
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

    comparison_record = None
    auto_blocked = False
    if comparison_memory:
        comparison_record = comparison_memory.record_result(
            query_category=query_category,
            seller_url=seller_url,
            seller_name=seller_name,
            quality_score=record.quality_score,
            roi=record.roi,
            purchase_id=record.id,
            reasoning=reasoning,
        )
        auto_blocked = comparison_memory.check_auto_block_score(
            seller_url, seller_name, record.quality_score,
        )

    log(_logger, "EVALUATE", "SCORED",
        f"{seller_name}: quality={record.quality_score}/8 roi={record.roi:.1f} "
        f"[rel={relevance} dep={depth} act={actionability} spec={specificity}]")

    lines = [
        f"Evaluation recorded for purchase from {seller_name}:",
        f"  Category: {query_category}",
        f"  Quality: {record.quality_score}/8 "
        f"(relevance={relevance}, depth={depth}, actionability={actionability}, specificity={specificity})",
        f"  Cost: {credits_spent} credit(s)",
        f"  ROI: {record.roi:.1f} (quality per credit)",
        f"  Reasoning: {reasoning}",
    ]

    if comparison_record:
        tested = [s for s in comparison_record.tested_sellers if s.attempts > 0]
        tested_count = len(tested)
        preferred = comparison_record.preferred_seller_url or "not decided yet"
        lines.extend([
            "",
            f"Category '{query_category}' comparison ({tested_count} seller(s) tested):",
        ])
        for s in tested:
            marker = " <-- BEST" if s.seller_url == comparison_record.preferred_seller_url else ""
            lines.append(
                f"  {s.seller_name}: score={s.quality_score:.1f}/8 "
                f"({s.attempts} attempt(s)){marker}"
            )
        if auto_blocked:
            lines.append(f"  ** {seller_name} AUTO-BLOCKED (score too low) — will be skipped in future selections.")
        if tested_count < 2:
            lines.append(f"  Need {2 - tested_count} more seller(s) before exploiting best.")
        elif comparison_record.needs_rebrowse:
            lines.append(f"  All scores below {comparison_record.minimum_acceptable_score:.0f}/8 — consider browsing for better sellers.")
        else:
            lines.append(f"  Preferred seller: {preferred}")

    log(_logger, "EVALUATE", "COMPARISON",
        f"category={query_category} tested={len(comparison_record.tested_sellers) if comparison_record else 0}")

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "purchase_id": record.id,
        "quality_score": record.quality_score,
        "roi": record.roi,
    }
