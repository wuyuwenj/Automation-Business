"""Thread-safe in-memory seller registry.

Stores seller agent cards and payment info discovered via A2A registration
or manual discovery. Used by the buyer agent to track available sellers.
"""

import threading
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class SellerInfo:
    """Parsed seller information from an agent card or marketplace."""

    url: str
    name: str
    description: str
    skills: list[dict]
    plan_id: str = ""
    agent_id: str = ""
    credits: int = 1
    cost_description: str = ""
    keywords: list[str] = field(default_factory=list)
    category: str = ""
    team_name: str = ""


class SellerRegistry:
    """Thread-safe in-memory registry of seller agents."""

    def __init__(self):
        self._sellers: dict[str, SellerInfo] = {}
        self._lock = threading.Lock()

    def register(self, agent_url: str, agent_card: dict) -> SellerInfo:
        """Parse an agent card and store seller info.

        Args:
            agent_url: The seller's base URL.
            agent_card: The full agent card dict (from /.well-known/agent.json).

        Returns:
            The stored SellerInfo.
        """
        url = agent_url.rstrip("/")

        name = agent_card.get("name", "Unknown Agent")
        description = agent_card.get("description", "")
        skills = agent_card.get("skills", [])

        # Extract payment extension
        plan_id = ""
        agent_id = ""
        credits = 1
        cost_description = ""

        extensions = agent_card.get("capabilities", {}).get("extensions", [])
        for ext in extensions:
            if ext.get("uri") == "urn:nevermined:payment":
                params = ext.get("params", {})
                plan_id = params.get("planId", "")
                agent_id = params.get("agentId", "")
                credits = params.get("credits", 1)
                cost_description = params.get("costDescription", "")
                break

        info = SellerInfo(
            url=url,
            name=name,
            description=description,
            skills=skills,
            plan_id=plan_id,
            agent_id=agent_id,
            credits=credits,
            cost_description=cost_description,
        )

        with self._lock:
            self._sellers[url] = info

        return info

    def register_from_marketplace(self, seller_data: dict) -> SellerInfo | None:
        """Register a seller from the Discovery API response format.

        Args:
            seller_data: A single seller dict from the Discovery API.

        Returns:
            SellerInfo if registered, None if endpoint is unreachable (localhost etc).
        """
        endpoint = seller_data.get("endpointUrl", "")
        if not endpoint:
            return None

        # Filter out localhost / internal endpoints
        parsed = urlparse(endpoint)
        hostname = parsed.hostname or ""
        if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "") or ":" not in endpoint[:10] and not endpoint.startswith("http"):
            return None
        if hostname == "seller" or ".local" in hostname:
            return None

        url = endpoint.rstrip("/")
        plan_ids = seller_data.get("planIds", [])
        pricing = seller_data.get("pricing", {})

        info = SellerInfo(
            url=url,
            name=seller_data.get("name", "Unknown"),
            description=seller_data.get("description", ""),
            skills=[{"name": kw} for kw in seller_data.get("keywords", [])[:5]],
            plan_id=plan_ids[0] if plan_ids else "",
            agent_id=seller_data.get("nvmAgentId", ""),
            credits=1,
            cost_description=pricing.get("perRequest", ""),
            keywords=seller_data.get("keywords", []),
            category=seller_data.get("category", ""),
            team_name=seller_data.get("teamName", ""),
        )

        with self._lock:
            self._sellers[url] = info

        return info

    def get_payment_info(self, agent_url: str) -> dict | None:
        """Get cached payment info for a seller (skips re-discovery).

        Args:
            agent_url: The seller's base URL.

        Returns:
            Dict with planId, agentId, credits, or None if not registered.
        """
        url = agent_url.rstrip("/")
        with self._lock:
            info = self._sellers.get(url)
        if not info:
            return None
        return {
            "planId": info.plan_id,
            "agentId": info.agent_id,
            "credits": info.credits,
        }

    def list_all(self) -> list[dict]:
        """Return a summary list of all registered sellers."""
        with self._lock:
            sellers = list(self._sellers.values())
        result = []
        for s in sellers:
            skill_names = [
                sk.get("name", sk.get("id", "unknown")) for sk in s.skills
            ]
            result.append({
                "url": s.url,
                "name": s.name,
                "description": s.description,
                "skills": skill_names,
                "credits": s.credits,
                "cost_description": s.cost_description,
                "keywords": s.keywords,
                "category": s.category,
                "team_name": s.team_name,
            })
        return result

    def get_first_url(self) -> str | None:
        """Return the URL of the first registered seller, or None."""
        with self._lock:
            if not self._sellers:
                return None
            return next(iter(self._sellers.values())).url

    def __len__(self) -> int:
        with self._lock:
            return len(self._sellers)
