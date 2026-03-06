"""OHLCV market analysis using CoinGecko + OpenAI synthesis."""

import os

import httpx
from openai import OpenAI

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
VALID_DAYS = {1, 7, 14, 30, 90, 180, 365}


def market_analysis_impl(token_id: str, days: int = 7) -> dict:
    """Fetch OHLCV data and produce an LLM-powered trend analysis.

    Args:
        token_id: CoinGecko token ID (e.g. "bitcoin").
        days: Number of days for OHLCV data (1, 7, 14, 30, 90, 180, 365).

    Returns:
        Dict with status, content, analysis text, and OHLCV summary.
    """
    # Clamp days to valid CoinGecko values
    if days not in VALID_DAYS:
        days = min(VALID_DAYS, key=lambda d: abs(d - days))

    try:
        with httpx.Client(timeout=15.0) as client:
            # Fetch OHLCV data
            ohlc_resp = client.get(
                f"{COINGECKO_BASE}/coins/{token_id}/ohlc",
                params={"vs_currency": "usd", "days": days},
            )
            ohlc_resp.raise_for_status()
            ohlc_data = ohlc_resp.json()

            # Fetch current market data
            market_resp = client.get(
                f"{COINGECKO_BASE}/coins/{token_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "community_data": "false",
                    "developer_data": "false",
                },
            )
            market_resp.raise_for_status()
            market_data = market_resp.json()

        if not ohlc_data:
            return {
                "status": "error",
                "content": [{"text": f"No OHLCV data found for {token_id}"}],
                "analysis": "",
                "ohlcv_summary": {},
            }

        # Calculate summary metrics from OHLCV
        opens = [d[1] for d in ohlc_data]
        highs = [d[2] for d in ohlc_data]
        lows = [d[3] for d in ohlc_data]
        closes = [d[4] for d in ohlc_data]

        ohlcv_summary = {
            "token": token_id,
            "period_days": days,
            "data_points": len(ohlc_data),
            "period_open": opens[0],
            "period_close": closes[-1],
            "period_high": max(highs),
            "period_low": min(lows),
            "price_change_pct": round(((closes[-1] - opens[0]) / opens[0]) * 100, 2),
            "current_price": market_data.get("market_data", {}).get("current_price", {}).get("usd"),
            "market_cap": market_data.get("market_data", {}).get("market_cap", {}).get("usd"),
            "total_volume": market_data.get("market_data", {}).get("total_volume", {}).get("usd"),
            "ath": market_data.get("market_data", {}).get("ath", {}).get("usd"),
            "ath_change_pct": market_data.get("market_data", {}).get("ath_change_percentage", {}).get("usd"),
        }

        # Build data summary for LLM
        data_text = f"""Token: {token_id.title()}
Period: {days} days ({len(ohlc_data)} data points)
Open: ${opens[0]:,.2f} -> Close: ${closes[-1]:,.2f} ({ohlcv_summary['price_change_pct']:+.2f}%)
Period High: ${max(highs):,.2f} | Period Low: ${min(lows):,.2f}
Current Price: ${ohlcv_summary['current_price']:,.2f}
Market Cap: ${ohlcv_summary['market_cap']:,.0f}
24h Volume: ${ohlcv_summary['total_volume']:,.0f}
ATH: ${ohlcv_summary['ath']:,.2f} ({ohlcv_summary['ath_change_pct']:.1f}% from ATH)

Recent OHLC (last 5 candles):
"""
        for candle in ohlc_data[-5:]:
            ts, o, h, l, c = candle
            data_text += f"  O=${o:,.2f} H=${h:,.2f} L=${l:,.2f} C=${c:,.2f}\n"

        # Send to OpenAI for analysis
        openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        completion = openai_client.chat.completions.create(
            model=os.environ.get("MODEL_ID", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a crypto market analyst. Analyze the OHLCV data and market metrics. "
                        "Provide a concise analysis including: 1) Trend direction (bullish/bearish/neutral), "
                        "2) Key support and resistance levels, 3) Volume analysis, "
                        "4) Brief outlook. Keep the analysis under 300 words."
                    ),
                },
                {"role": "user", "content": data_text},
            ],
            max_tokens=500,
        )
        analysis = completion.choices[0].message.content

        return {
            "status": "success",
            "content": [{"text": analysis}],
            "analysis": analysis,
            "ohlcv_summary": ohlcv_summary,
        }

    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "content": [{"text": f"CoinGecko API error: HTTP {exc.response.status_code}"}],
            "analysis": "",
            "ohlcv_summary": {},
        }
    except Exception as exc:
        return {
            "status": "error",
            "content": [{"text": f"Market analysis failed: {exc}"}],
            "analysis": "",
            "ohlcv_summary": {},
        }
