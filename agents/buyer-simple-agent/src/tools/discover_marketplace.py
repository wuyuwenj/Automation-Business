"""Discover sellers from the Nevermined hackathon marketplace Discovery API.

Queries the public Discovery API, filters out unreachable endpoints,
and auto-registers live sellers into the local SellerRegistry.
"""

import httpx

from ..log import get_logger, log

_logger = get_logger("buyer.marketplace")


def discover_marketplace_impl(
    nvm_api_key: str,
    seller_registry,
    category: str = "",
) -> dict:
    """Query the hackathon Discovery API and register live sellers.

    Args:
        nvm_api_key: Nevermined API key for authentication.
        seller_registry: SellerRegistry instance to register sellers into.
        category: Optional category filter (e.g. "DeFi", "AI/ML").

    Returns:
        Dict with status, registered sellers count, and seller list.
    """
    base_url = "https://nevermined.ai/hackathon/register/api/discover"
    params = {"side": "sell"}
    if category:
        params["category"] = category

    log(_logger, "MARKETPLACE", "DISCOVER", f"querying Discovery API category={category or 'all'}")

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                base_url,
                params=params,
                headers={"x-nvm-api-key": nvm_api_key},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        log(_logger, "MARKETPLACE", "ERROR", f"API returned {e.response.status_code}")
        return {
            "status": "error",
            "content": [{"text": f"Discovery API error: HTTP {e.response.status_code}"}],
        }
    except Exception as e:
        log(_logger, "MARKETPLACE", "ERROR", f"connection failed: {e}")
        return {
            "status": "error",
            "content": [{"text": f"Failed to connect to Discovery API: {e}"}],
        }

    sellers = data.get("sellers", [])
    total = data.get("meta", {}).get("total", len(sellers))
    log(_logger, "MARKETPLACE", "FOUND", f"{total} total sellers in marketplace")

    registered = []
    skipped = 0

    for seller_data in sellers:
        info = seller_registry.register_from_marketplace(seller_data)
        if info:
            registered.append({
                "name": info.name,
                "url": info.url,
                "category": info.category,
                "team": info.team_name,
                "cost": info.cost_description,
                "keywords": info.keywords[:5],
            })
            log(_logger, "MARKETPLACE", "REGISTERED",
                f"{info.name} ({info.category}) @ {info.url}")
        else:
            skipped += 1

    log(_logger, "MARKETPLACE", "COMPLETED",
        f"registered={len(registered)} skipped={skipped} (localhost/unreachable)")

    lines = [f"Marketplace discovery complete: {len(registered)} live sellers found ({skipped} skipped as unreachable).\n"]
    for s in registered:
        lines.append(f"  {s['name']} [{s['category']}] - {s['cost']}")
        lines.append(f"    Team: {s['team']}")
        lines.append(f"    URL: {s['url']}")
        lines.append(f"    Keywords: {', '.join(s['keywords'])}")
        lines.append("")

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "registered_count": len(registered),
        "skipped_count": skipped,
        "sellers": registered,
    }
