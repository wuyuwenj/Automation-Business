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


def _run_liveness_check(urls: list[str]) -> dict[str, bool]:
    """Run parallel liveness pings (sync wrapper)."""
    if not urls:
        return {}
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're already in an async context, use a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_ping_all(urls))).result()
        return loop.run_until_complete(_ping_all(urls))
    except RuntimeError:
        return asyncio.run(_ping_all(urls))


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
        liveness = _run_liveness_check(urls)

        alive = []
        dead = 0
        for s in registered:
            if liveness.get(s["url"], False):
                alive.append(s)
                log(_logger, "MARKETPLACE", "ALIVE", f"{s['name']} @ {s['url']}")
            else:
                dead += 1
                log(_logger, "MARKETPLACE", "DEAD", f"{s['name']} @ {s['url']}")
                # Remove dead sellers from the registry
                seller_registry.remove(s["url"])

        registered = alive
        log(_logger, "MARKETPLACE", "COMPLETED",
            f"alive={len(registered)} dead={dead} skipped={skipped}")
    else:
        log(_logger, "MARKETPLACE", "COMPLETED",
            f"registered=0 skipped={skipped}")

    lines = [f"Marketplace discovery complete: {len(registered)} live sellers found ({skipped} skipped as localhost).\n"]
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
