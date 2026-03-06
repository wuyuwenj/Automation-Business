"""Real-time crypto price check using CoinGecko API (free, no key required)."""

import httpx

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


def price_check_impl(token_ids: str, vs_currencies: str = "usd") -> dict:
    """Get real-time prices for one or more crypto tokens.

    Args:
        token_ids: Comma-separated CoinGecko token IDs (e.g. "bitcoin,ethereum,solana").
        vs_currencies: Comma-separated fiat currencies (default: "usd").

    Returns:
        Dict with status, content (Strands format), and prices list.
    """
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{COINGECKO_BASE}/simple/price",
                params={
                    "ids": token_ids,
                    "vs_currencies": vs_currencies,
                    "include_24hr_change": "true",
                    "include_market_cap": "true",
                    "include_24hr_vol": "true",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if not data:
            return {
                "status": "error",
                "content": [{"text": f"No price data found for: {token_ids}"}],
                "prices": [],
            }

        prices = []
        text_parts = []
        currencies = [c.strip() for c in vs_currencies.split(",")]

        for token_id, token_data in data.items():
            for currency in currencies:
                price = token_data.get(currency)
                change_24h = token_data.get(f"{currency}_24h_change")
                market_cap = token_data.get(f"{currency}_market_cap")
                volume_24h = token_data.get(f"{currency}_24h_vol")

                if price is None:
                    continue

                price_info = {
                    "token": token_id,
                    "currency": currency,
                    "price": price,
                    "change_24h_pct": round(change_24h, 2) if change_24h else None,
                    "market_cap": market_cap,
                    "volume_24h": volume_24h,
                }
                prices.append(price_info)

                change_str = f"{change_24h:+.2f}%" if change_24h else "N/A"
                cap_str = _format_large_number(market_cap) if market_cap else "N/A"
                vol_str = _format_large_number(volume_24h) if volume_24h else "N/A"

                text_parts.append(
                    f"- **{token_id.title()}** ({currency.upper()}): "
                    f"${price:,.2f} | 24h: {change_str} | "
                    f"MCap: {cap_str} | Vol: {vol_str}"
                )

        text = "## Crypto Prices\n\n" + "\n".join(text_parts)
        return {
            "status": "success",
            "content": [{"text": text}],
            "prices": prices,
        }

    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "content": [{"text": f"CoinGecko API error: HTTP {exc.response.status_code}"}],
            "prices": [],
        }
    except Exception as exc:
        return {
            "status": "error",
            "content": [{"text": f"Price check failed: {exc}"}],
            "prices": [],
        }


def _format_large_number(n: float) -> str:
    """Format large numbers for readability (e.g. 1.4T, 250B, 12M)."""
    if n >= 1e12:
        return f"${n / 1e12:.2f}T"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    return f"${n:,.0f}"
