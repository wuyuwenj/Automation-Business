"""
Plain Strands tools WITHOUT @requires_payment -- for A2A mode.

In A2A mode, payment validation and credit settlement happen at the A2A
message level via PaymentsRequestHandler. Individual tools don't need
the @requires_payment decorator.
"""

from a2a.types import AgentSkill
from strands import Agent, tool

from .tools.price_check import price_check_impl
from .tools.market_analysis import market_analysis_impl
from .tools.defi_report import defi_report_impl


# ---------------------------------------------------------------------------
# Plain Strands tools (no payment decorator)
# ---------------------------------------------------------------------------

@tool
def price_check(token_ids: str, vs_currencies: str = "usd") -> dict:
    """Get real-time crypto/token prices with 24h change and market cap. Costs 1 credit per request.

    Args:
        token_ids: Comma-separated CoinGecko token IDs (e.g. "bitcoin,ethereum,solana").
        vs_currencies: Comma-separated fiat currencies (default: "usd").
    """
    return price_check_impl(token_ids, vs_currencies)


@tool
def market_analysis(token_id: str, days: int = 7) -> dict:
    """OHLCV market data with LLM-powered trend analysis. Costs 5 credits per request.

    Args:
        token_id: CoinGecko token ID (e.g. "bitcoin").
        days: Number of days for OHLCV data (1, 7, 14, 30, 90, 180, 365).
    """
    return market_analysis_impl(token_id, days)


@tool
def defi_report(query: str, top_n: int = 10) -> dict:
    """Full DeFi protocol report with TVL analysis and trends. Costs 10 credits per request.

    Args:
        query: Protocol name, category, or chain to research (e.g. "lending", "uniswap", "arbitrum").
        top_n: Number of top protocols to include (default: 10).
    """
    return defi_report_impl(query, top_n)


# ---------------------------------------------------------------------------
# ALL_TOOLS registry
# ---------------------------------------------------------------------------

ALL_TOOLS = {
    "price": {
        "tool": price_check,
        "credits": 1,
        "skill": AgentSkill(
            id="price_check",
            name="Crypto Price Check",
            description="Get real-time crypto/token prices with 24h change and market cap. Costs 1 credit.",
            tags=["crypto", "price", "defi", "market"],
        ),
    },
    "analysis": {
        "tool": market_analysis,
        "credits": 5,
        "skill": AgentSkill(
            id="market_analysis",
            name="Market Analysis",
            description="OHLCV data with LLM-powered trend analysis for any crypto token. Costs 5 credits.",
            tags=["crypto", "analysis", "ohlcv", "trends"],
        ),
    },
    "report": {
        "tool": defi_report,
        "credits": 10,
        "skill": AgentSkill(
            id="defi_report",
            name="DeFi Report",
            description="Full DeFi protocol report with TVL rankings and trend analysis. Costs 10 credits.",
            tags=["defi", "report", "tvl", "protocols"],
        ),
    },
}


def resolve_tools(tool_names: list[str] | None = None):
    """Resolve tool short names to (tools, credit_map, skills)."""
    names = tool_names if tool_names else list(ALL_TOOLS.keys())
    tools = []
    credit_map = {}
    skills = []
    for name in names:
        entry = ALL_TOOLS[name]
        fn = entry["tool"]
        tools.append(fn)
        credit_map[fn.__name__] = entry["credits"]
        skills.append(entry["skill"])
    return tools, credit_map, skills


# Module-level defaults
CREDIT_MAP = {fn.__name__: e["credits"] for fn, e in
               ((ALL_TOOLS[n]["tool"], ALL_TOOLS[n]) for n in ALL_TOOLS)}
TOOLS = [ALL_TOOLS[n]["tool"] for n in ALL_TOOLS]


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a crypto and DeFi market intelligence agent. You provide market data \
services at three pricing tiers:

1. **price_check** (1 credit) - Real-time crypto prices. Use for quick price lookups.
2. **market_analysis** (5 credits) - OHLCV data with trend analysis. Use for \
technical analysis requests.
3. **defi_report** (10 credits) - Full DeFi protocol report. Use for comprehensive \
DeFi research.

Choose the appropriate tool based on the user's request. For simple price queries, \
use price_check. For trend/technical analysis, use market_analysis. For DeFi \
protocol research, use defi_report.

Always be helpful and explain the market data you found."""


def _build_system_prompt(tools_list):
    """Build a system prompt that only mentions the available tools."""
    tool_names = {t.__name__ for t in tools_list}
    lines = ["You are a crypto and DeFi market intelligence agent. You provide market data services:\n"]
    if "price_check" in tool_names:
        lines.append("- **price_check** (1 credit) - Real-time crypto prices.")
    if "market_analysis" in tool_names:
        lines.append("- **market_analysis** (5 credits) - OHLCV data with trend analysis.")
    if "defi_report" in tool_names:
        lines.append("- **defi_report** (10 credits) - Full DeFi protocol report.")
    lines.append(
        "\nChoose the appropriate tool based on the user's request. "
        "Always be helpful and explain the market data you found."
    )
    return "\n".join(lines)


def create_plain_agent(model, tool_names: list[str] | None = None) -> Agent:
    """Create a Strands agent with plain (non-payment) tools."""
    if tool_names:
        tools, _, _ = resolve_tools(tool_names)
        prompt = _build_system_prompt(tools)
    else:
        tools = TOOLS
        prompt = SYSTEM_PROMPT
    return Agent(
        model=model,
        tools=tools,
        system_prompt=prompt,
    )
