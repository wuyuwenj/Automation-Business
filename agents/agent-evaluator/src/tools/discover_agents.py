"""Discover agents via the hackathon Discovery API."""

import os
import httpx

DISCOVERY_URL = os.getenv("DISCOVERY_API_URL", "https://nevermined.ai/hackathon/register/api/discover")


def discover_agents_impl(category: str = "", side: str = "sell") -> dict:
    """Query the hackathon Discovery API for registered agents.

    Args:
        category: Filter by category (e.g. "DeFi", "AI/ML", "Infrastructure").
        side: Filter by side - "sell", "buy", or "" for both.
    """
    nvm_api_key = os.environ.get("NVM_API_KEY", "")
    if not nvm_api_key:
        return {"status": "error", "content": [{"text": "NVM_API_KEY required for Discovery API"}], "agents": [], "total": 0}

    try:
        params = {}
        if side:
            params["side"] = side
        if category:
            params["category"] = category

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                DISCOVERY_URL,
                headers={"x-nvm-api-key": nvm_api_key},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        sellers = data.get("sellers", [])
        buyers = data.get("buyers", [])
        meta = data.get("meta", {})

        # Build agent summaries
        agents = []
        text_parts = []

        if side != "buy":
            for s in sellers:
                agent_info = {
                    "name": s.get("name", ""),
                    "team": s.get("teamName", ""),
                    "category": s.get("category", ""),
                    "description": s.get("description", ""),
                    "services": s.get("servicesSold", ""),
                    "pricing": s.get("pricing", {}),
                    "endpoint": s.get("endpointUrl", ""),
                    "side": "seller",
                }
                agents.append(agent_info)
                pricing_str = agent_info["pricing"].get("perRequest", "N/A") if isinstance(agent_info["pricing"], dict) else "N/A"
                text_parts.append(
                    f"- **{agent_info['name']}** ({agent_info['category']}) by {agent_info['team']}\n"
                    f"  Services: {agent_info['services']}\n"
                    f"  Price: {pricing_str}\n"
                    f"  Endpoint: {agent_info['endpoint']}"
                )

        if side != "sell":
            for b in buyers:
                agent_info = {
                    "name": b.get("name", ""),
                    "team": b.get("teamName", ""),
                    "category": b.get("category", ""),
                    "description": b.get("description", ""),
                    "interests": b.get("interests", ""),
                    "side": "buyer",
                }
                agents.append(agent_info)
                text_parts.append(
                    f"- **{agent_info['name']}** ({agent_info['category']}) by {agent_info['team']}\n"
                    f"  Interests: {agent_info['interests']}"
                )

        filter_desc = f"side={side}" if side else "all"
        if category:
            filter_desc += f", category={category}"

        text = (
            f"## Hackathon Agent Discovery\n\n"
            f"**Filter:** {filter_desc}\n"
            f"**Total agents found:** {len(agents)}\n\n"
            + "\n\n".join(text_parts)
        )

        return {
            "status": "success",
            "content": [{"text": text}],
            "agents": agents,
            "total": len(agents),
            "filter_applied": filter_desc,
        }

    except httpx.HTTPStatusError as exc:
        return {"status": "error", "content": [{"text": f"Discovery API error: HTTP {exc.response.status_code}"}], "agents": [], "total": 0}
    except Exception as exc:
        return {"status": "error", "content": [{"text": f"Discovery failed: {exc}"}], "agents": [], "total": 0}
