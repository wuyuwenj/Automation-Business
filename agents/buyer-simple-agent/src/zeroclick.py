"""Helpers for dynamic ZeroClick ad targeting from buyer web messages."""

from __future__ import annotations

import hashlib
import re
from typing import Any


_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "DeFi": ("defi", "lending protocol", "dex", "yield", "staking", "arbitrum", "solana", "ethereum", "crypto"),
    "AI agents": ("agent", "agents", "automation", "llm", "openai", "claude", "chatbot", "ai"),
    "Developer tools": ("api", "sdk", "database", "postgres", "supabase", "deployment", "ci/cd", "devtools"),
    "Marketing": ("ads", "ad copy", "seo", "email marketing", "landing page", "growth", "campaign"),
    "Real estate": ("real estate", "realtor", "broker", "property", "housing"),
    "Ecommerce": ("ecommerce", "shopify", "store", "checkout", "conversion"),
}


def build_offer_query(message: str, default_query: str) -> str:
    """Return the query used to fetch dynamic offers."""
    cleaned = " ".join(message.strip().split())
    if len(cleaned) < 3:
        return default_query
    return cleaned[:180]


def build_session_user_id(session_id: str, ip_address: str) -> str:
    """Hash the transient session context into a privacy-safe user identifier."""
    raw = (session_id or ip_address or "anonymous").strip()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"buyer-web-{digest}"


def infer_signals(message: str) -> list[dict[str, Any]]:
    """Infer lightweight commercial-intent signals from one user message."""
    text = " ".join(message.strip().split())
    if len(text) < 3:
        return []

    lower = text.lower()
    topics = _detect_topics(lower)
    subject = topics[0] if topics else _extract_subject(text)
    sentiment = _infer_sentiment(lower)
    attributes = _extract_attributes(text, lower)

    signals: list[dict[str, Any]] = []
    primary_category, confidence = _infer_primary_category(lower)
    signals.append({
        "category": primary_category,
        "confidence": confidence,
        "subject": subject[:500],
        "relatedSubjects": topics[:10],
        "sentiment": sentiment,
        "attributes": attributes,
        "extractionReason": "Derived from the current buyer web chat message.",
        "sourceContext": text[:2000],
    })

    if attributes.get("budget") and primary_category != "price_sensitivity":
        signals.append({
            "category": "price_sensitivity",
            "confidence": 0.78,
            "subject": subject[:500],
            "relatedSubjects": topics[:10],
            "sentiment": sentiment,
            "attributes": {"budget": attributes["budget"]},
            "extractionReason": "Budget language was detected in the current message.",
            "sourceContext": text[:2000],
        })

    return signals[:10]


def _detect_topics(text: str) -> list[str]:
    topics: list[str] = []
    for label, keywords in _TOPIC_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            topics.append(label)
    return topics


def _extract_subject(text: str) -> str:
    lowered = text.lower()
    for marker in ("about ", "for ", "compare ", "buy ", "need ", "want "):
        idx = lowered.find(marker)
        if idx >= 0:
            subject = text[idx + len(marker):].strip(" .,:;")
            if subject:
                return subject[:120]
    return text[:120]


def _infer_primary_category(text: str) -> tuple[str, float]:
    if any(term in text for term in ("under $", "budget", "cheap", "affordable", "lower cost", "price")):
        return ("price_sensitivity", 0.84)
    if any(term in text for term in ("buy", "purchase", "subscribe", "sign up", "looking to get")):
        return ("purchase_intent", 0.9)
    if any(term in text for term in ("compare", "vs", "versus", "better than", "alternatives")):
        return ("evaluation", 0.87)
    if any(term in text for term in ("recommend", "suggest", "what tool", "which one", "best option")):
        return ("recommendation_request", 0.9)
    if any(term in text for term in ("problem", "too slow", "stuck", "pain", "issue", "need help", "broken")):
        return ("problem", 0.83)
    if any(term in text for term in ("prefer", "love", "like ", "fan of")):
        return ("brand_affinity", 0.72)
    if any(term in text for term in ("team", "startup", "enterprise", "client", "company", "b2b", "workflow")):
        return ("business_context", 0.68)
    return ("interest", 0.74)


def _infer_sentiment(text: str) -> str:
    if any(term in text for term in ("too slow", "stuck", "bad", "hate", "problem", "issue", "expensive")):
        return "negative"
    if any(term in text for term in ("want", "buy", "love", "need", "looking for", "interested in", "prefer")):
        return "positive"
    return "neutral"


def _extract_attributes(text: str, lowered: str) -> dict[str, str]:
    attributes: dict[str, str] = {}

    budget_match = re.search(r"(under\s+\$?\d[\d,]*(?:\s*[kKmM])?)", text)
    if budget_match:
        attributes["budget"] = budget_match.group(1)
    elif "$" in text:
        price_match = re.search(r"(\$\d[\d,]*(?:\s*[kKmM])?)", text)
        if price_match:
            attributes["budget"] = price_match.group(1)

    use_case_match = re.search(r"\bfor\s+([^.,;\n]{3,80})", text, re.IGNORECASE)
    if use_case_match:
        attributes["use_case"] = use_case_match.group(1).strip()

    if any(term in lowered for term in ("enterprise", "large team")):
        attributes["team_size"] = "enterprise"
    elif any(term in lowered for term in ("small team", "startup", "founder")):
        attributes["team_size"] = "small-team"

    if "real estate" in lowered:
        attributes["industry"] = "real-estate"
    elif "fintech" in lowered:
        attributes["industry"] = "fintech"
    elif "ecommerce" in lowered or "shopify" in lowered:
        attributes["industry"] = "ecommerce"

    return attributes
