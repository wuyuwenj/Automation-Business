"""Pre-purchase seller filtering using embeddings with keyword fallback.

Ranks sellers by semantic similarity to the user's query using OpenAI
embeddings. Falls back to keyword/category matching if embeddings fail.
"""

import math
import threading

from openai import OpenAI

from ..log import get_logger, log
from ..openai_compat import EMBEDDING_MODEL
from ..registry import SellerRegistry

_logger = get_logger("buyer.filter")

# ---------------------------------------------------------------------------
# Embedding cache — seller embeddings are stable within a session
# ---------------------------------------------------------------------------

_embedding_cache: dict[str, list[float]] = {}
_cache_lock = threading.Lock()


def _build_seller_text(seller: dict) -> str:
    """Build a single text string from seller metadata for embedding."""
    parts = [seller.get("name", "")]
    if seller.get("description"):
        parts.append(seller["description"])
    if seller.get("category"):
        parts.append(f"Category: {seller['category']}")
    if seller.get("keywords"):
        kws = seller["keywords"]
        if isinstance(kws, list):
            parts.append(f"Keywords: {', '.join(str(k) for k in kws)}")
    if seller.get("skills"):
        skills = seller["skills"]
        if isinstance(skills, list):
            parts.append(f"Skills: {', '.join(str(s) for s in skills)}")
    return ". ".join(parts)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity (no numpy dependency)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Embedding-based scoring
# ---------------------------------------------------------------------------

def _score_sellers_embedding(
    query: str,
    sellers: list[dict],
    client: OpenAI,
    model: str = EMBEDDING_MODEL,
) -> list[dict]:
    """Score sellers by cosine similarity to the query using embeddings."""
    # Collect sellers whose embeddings are not yet cached
    uncached_texts: list[str] = []
    uncached_urls: list[str] = []
    for s in sellers:
        url = s.get("url", "")
        with _cache_lock:
            if url not in _embedding_cache:
                uncached_texts.append(_build_seller_text(s))
                uncached_urls.append(url)

    # Batch-embed uncached sellers (single API call)
    if uncached_texts:
        resp = client.embeddings.create(input=uncached_texts, model=model)
        with _cache_lock:
            for url, item in zip(uncached_urls, resp.data):
                _embedding_cache[url] = item.embedding
        log(_logger, "FILTER", "EMBED",
            f"cached {len(uncached_texts)} new seller embeddings")

    # Embed the query (1 API call)
    query_resp = client.embeddings.create(input=[query], model=model)
    query_emb = query_resp.data[0].embedding

    # Score each seller
    scored = []
    for s in sellers:
        url = s.get("url", "")
        with _cache_lock:
            seller_emb = _embedding_cache.get(url)
        sim = _cosine_similarity(query_emb, seller_emb) if seller_emb else 0.0
        scored.append({
            **s,
            "relevance_score": round(sim, 4),
            "match_reasons": [f"embedding similarity: {sim:.3f}"],
        })

    scored.sort(key=lambda s: s["relevance_score"], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Keyword-based scoring (fallback)
# ---------------------------------------------------------------------------

def _score_sellers_keyword(query: str, sellers: list[dict]) -> list[dict]:
    """Attach relevance scores using keyword/category overlap (fallback)."""
    query_lower = query.lower()
    query_words = set(query_lower.split())

    scored = []
    for seller in sellers:
        score = 0
        reasons = []

        seller_keywords = [kw.lower() for kw in seller.get("keywords", [])]
        matching_keywords = [kw for kw in seller_keywords if kw in query_lower]
        if matching_keywords:
            score += len(matching_keywords) * 2
            reasons.append(f"keywords: {', '.join(matching_keywords)}")

        category = seller.get("category", "").lower()
        if category:
            if category in query_lower or any(w in category for w in query_words):
                score += 3
                reasons.append(f"category: {seller.get('category')}")
            elif any(kw in category for kw in seller_keywords[:3]):
                score += 1

        desc_words = set(seller.get("description", "").lower().split())
        overlap = query_words & desc_words
        overlap_count = min(len(overlap), 5)
        if overlap_count > 0:
            score += overlap_count
            reasons.append(f"description overlap: {overlap_count} words")

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rank_sellers_for_query(
    query: str,
    seller_registry: SellerRegistry | None = None,
    sellers: list[dict] | None = None,
    max_results: int = 3,
    embedding_client: OpenAI | None = None,
) -> list[dict]:
    """Rank sellers by relevance. Uses embeddings if available, else keywords."""
    seller_rows = sellers if sellers is not None else (
        seller_registry.list_all(verbose=True) if seller_registry else []
    )
    if not seller_rows:
        return []

    if embedding_client:
        try:
            scored = _score_sellers_embedding(query, seller_rows, embedding_client)
            log(_logger, "FILTER", "METHOD", "embedding")
            return scored[:max_results]
        except Exception as e:
            log(_logger, "FILTER", "FALLBACK",
                f"embedding failed ({e}), using keyword matching")

    scored = _score_sellers_keyword(query, seller_rows)
    log(_logger, "FILTER", "METHOD", "keyword")
    return scored[:max_results]


def filter_sellers_impl(
    query: str,
    seller_registry: SellerRegistry,
    embedding_client: OpenAI | None = None,
) -> dict:
    """Find the most relevant sellers for a query."""
    top = rank_sellers_for_query(
        query, seller_registry=seller_registry, max_results=3,
        embedding_client=embedding_client,
    )
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
        lines.append(
            f"  {i+1}. {s['name']} score={s['relevance_score']} "
            f"{s['credits']}cr ({s['url']})"
        )

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
