"""Full DeFi protocol report using DeFiLlama + CoinGecko + OpenAI."""

import os

import httpx
from openai import OpenAI

DEFILLAMA_BASE = "https://api.llama.fi"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"


def defi_report_impl(query: str, top_n: int = 10) -> dict:
    """Generate a full DeFi protocol report.

    Args:
        query: Protocol name, category, or chain to research
               (e.g. "lending", "uniswap", "arbitrum").
        top_n: Number of top protocols to include (default: 10).

    Returns:
        Dict with status, content, report text, protocols list, and sources.
    """
    try:
        with httpx.Client(timeout=20.0) as client:
            # Fetch all protocols from DeFiLlama
            protocols_resp = client.get(f"{DEFILLAMA_BASE}/protocols")
            protocols_resp.raise_for_status()
            all_protocols = protocols_resp.json()

            # Fetch chain TVL data
            chains_resp = client.get(f"{DEFILLAMA_BASE}/v2/chains")
            chains_resp.raise_for_status()
            chains_data = chains_resp.json()

        # Filter protocols by query (name, category, or chain)
        query_lower = query.lower()
        matched = []
        for p in all_protocols:
            name = (p.get("name") or "").lower()
            category = (p.get("category") or "").lower()
            chains = [c.lower() for c in (p.get("chains") or [])]
            slug = (p.get("slug") or "").lower()

            if (
                query_lower in name
                or query_lower in category
                or query_lower in slug
                or any(query_lower in c for c in chains)
            ):
                matched.append(p)

        # Sort by TVL descending, take top N
        matched.sort(key=lambda p: p.get("tvl") or 0, reverse=True)
        top_protocols = matched[:top_n]

        if not top_protocols:
            return {
                "status": "error",
                "content": [{"text": f"No DeFi protocols found matching: {query}"}],
                "report": "",
                "protocols": [],
                "sources": ["DeFiLlama"],
            }

        # Build protocol summaries
        protocol_summaries = []
        for p in top_protocols:
            tvl = p.get("tvl") or 0
            change_1d = p.get("change_1d")
            change_7d = p.get("change_7d")
            protocol_summaries.append({
                "name": p.get("name"),
                "category": p.get("category"),
                "tvl": tvl,
                "tvl_formatted": _format_tvl(tvl),
                "change_1d": round(change_1d, 2) if change_1d else None,
                "change_7d": round(change_7d, 2) if change_7d else None,
                "chains": p.get("chains", []),
                "slug": p.get("slug"),
            })

        # Find relevant chain TVL data
        chain_context = ""
        for chain in chains_data:
            if query_lower in (chain.get("name") or "").lower():
                chain_context += (
                    f"\nChain: {chain.get('name')} | "
                    f"TVL: ${chain.get('tvl', 0):,.0f}\n"
                )

        # Build data for LLM synthesis
        data_text = f"Query: {query}\n\nTop {len(protocol_summaries)} protocols:\n\n"
        for i, p in enumerate(protocol_summaries, 1):
            change_1d_str = f"{p['change_1d']:+.2f}%" if p['change_1d'] else "N/A"
            change_7d_str = f"{p['change_7d']:+.2f}%" if p['change_7d'] else "N/A"
            data_text += (
                f"{i}. {p['name']} ({p['category']})\n"
                f"   TVL: {p['tvl_formatted']} | "
                f"1d: {change_1d_str} | 7d: {change_7d_str}\n"
                f"   Chains: {', '.join(p['chains'][:5])}\n\n"
            )
        data_text += chain_context

        # Send to OpenAI for report synthesis
        openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        completion = openai_client.chat.completions.create(
            model=os.environ.get("MODEL_ID", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a DeFi analyst. Based on the protocol data, TVL metrics, "
                        "and trends, write a concise DeFi report including:\n"
                        "1. Executive Summary (2-3 sentences)\n"
                        "2. Top Protocols by TVL (brief overview)\n"
                        "3. Trends (TVL changes, category dynamics)\n"
                        "4. Risk Factors\n"
                        "5. Recommendations\n"
                        "Keep the report under 500 words."
                    ),
                },
                {"role": "user", "content": data_text},
            ],
            max_tokens=800,
        )
        report = completion.choices[0].message.content

        return {
            "status": "success",
            "content": [{"text": report}],
            "report": report,
            "protocols": protocol_summaries,
            "sources": ["DeFiLlama", "CoinGecko"],
        }

    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "content": [{"text": f"API error: HTTP {exc.response.status_code}"}],
            "report": "",
            "protocols": [],
            "sources": [],
        }
    except Exception as exc:
        return {
            "status": "error",
            "content": [{"text": f"DeFi report failed: {exc}"}],
            "report": "",
            "protocols": [],
            "sources": [],
        }


def _format_tvl(tvl: float) -> str:
    """Format TVL for readability."""
    if tvl >= 1e9:
        return f"${tvl / 1e9:.2f}B"
    if tvl >= 1e6:
        return f"${tvl / 1e6:.2f}M"
    if tvl >= 1e3:
        return f"${tvl / 1e3:.1f}K"
    return f"${tvl:,.0f}"
