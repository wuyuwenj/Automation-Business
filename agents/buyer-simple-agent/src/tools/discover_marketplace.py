"""Discover sellers from the Nevermined hackathon marketplace Discovery API.

Queries the public Discovery API, filters out unreachable endpoints,
pings all endpoints in parallel to verify liveness, and auto-registers
live sellers into the local SellerRegistry.
"""

import asyncio

import httpx

from ..log import get_logger, log

_logger = get_logger("buyer.marketplace")

# Short timeout for liveness pings — we just need to know the server responds
_PING_TIMEOUT = 5.0


async def _ping_one(url: str) -> tuple[str, bool]:
    """Ping a single endpoint URL. Returns (url, is_alive)."""
    try:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT, verify=False) as client:
            resp = await client.get(url)
            # Any response (200, 401, 405, 422) means the server is alive.
            # Only connection errors / timeouts mean it's dead.
            return (url, True)
    except Exception:
        return (url, False)


async def _ping_all(urls: list[str]) -> dict[str, bool]:
    """Ping all URLs concurrently. Returns {url: is_alive}."""
    tasks = [_ping_one(url) for url in urls]
    results = await asyncio.gather(*tasks)
    return dict(results)


async def _fetch_agent_card(url: str) -> tuple[str, dict | None]:
    """Fetch /.well-known/agent.json from a seller. Returns (url, card_or_None)."""
    base = url.rstrip("/")
    card_url = f"{base}/.well-known/agent.json"
    try:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT, verify=False) as client:
            resp = await client.get(card_url)
            if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                return (url, resp.json())
    except Exception:
        pass
    return (url, None)


async def _fetch_all_agent_cards(urls: list[str]) -> dict[str, dict | None]:
    """Fetch agent cards from all URLs concurrently."""
    tasks = [_fetch_agent_card(url) for url in urls]
    results = await asyncio.gather(*tasks)
    return dict(results)


def _run_async(coro):
    """Run an async coroutine from sync code (handles existing event loops)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(coro)).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


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
        else:
            skipped += 1

    # Parallel liveness check — ping all registered endpoints concurrently
    if registered:
        urls = [s["url"] for s in registered]
        log(_logger, "MARKETPLACE", "PING",
            f"checking {len(urls)} endpoints in parallel (timeout={_PING_TIMEOUT}s)...")
        liveness = _run_async(_ping_all(urls))

        alive = []
        dead = 0
        for s in registered:
            if liveness.get(s["url"], False):
                alive.append(s)
            else:
                dead += 1
                seller_registry.remove(s["url"])

        registered = alive

        # Fetch agent cards in parallel to get correct plan/agent IDs.
        # The Discovery API plan IDs often differ from the ones in agent cards,
        # and agent cards are the only source of agentId for many sellers.
        if registered:
            card_urls = [s["url"] for s in registered]
            cards = _run_async(_fetch_all_agent_cards(card_urls))
            cards_found = 0
            for s_url, card in cards.items():
                if not card:
                    continue
                extensions = card.get("capabilities", {}).get("extensions", [])
                for ext in extensions:
                    if ext.get("uri") == "urn:nevermined:payment":
                        params = ext.get("params", {})
                        card_plan = params.get("planId", "")
                        card_agent = params.get("agentId", "")
                        if card_plan or card_agent:
                            seller_registry.update_payment_info(
                                s_url, card_plan, card_agent)
                            cards_found += 1
                        break
            log(_logger, "MARKETPLACE", "CARDS",
                f"{cards_found}/{len(card_urls)} agent cards resolved")

        log(_logger, "MARKETPLACE", "COMPLETED",
            f"alive={len(registered)} dead={dead} skipped={skipped}")
    else:
        log(_logger, "MARKETPLACE", "COMPLETED",
            f"registered=0 skipped={skipped}")

    # Ultra-compact summary — full details available via filter_sellers / list_sellers.
    lines = [f"Marketplace: {len(registered)} live sellers registered ({skipped} skipped)."]
    if registered:
        sample = registered[:3]
        names = ", ".join(s["name"] for s in sample)
        lines.append(f"  e.g. {names}")
        if len(registered) > 3:
            lines.append(f"  ... and {len(registered) - 3} more. Use filter_sellers to find relevant ones.")

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "registered_count": len(registered),
        "skipped_count": skipped,
    }
