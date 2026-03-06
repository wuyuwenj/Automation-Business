"""
Strands agent with Nevermined x402 payment-protected tools.

Used by agent.py (FastAPI HTTP mode). Tools use @requires_payment decorator.
"""

import os

from dotenv import load_dotenv
from strands import Agent, tool

from payments_py import Payments, PaymentOptions
from payments_py.x402.strands import requires_payment

from .tools.price_check import price_check_impl
from .tools.market_analysis import market_analysis_impl
from .tools.defi_report import defi_report_impl

load_dotenv()

NVM_API_KEY = os.environ["NVM_API_KEY"]
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID = os.environ["NVM_PLAN_ID"]
NVM_AGENT_ID = os.getenv("NVM_AGENT_ID")

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)


# ---------------------------------------------------------------------------
# Payment-protected Strands tools
# ---------------------------------------------------------------------------

@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=1,
    agent_id=NVM_AGENT_ID,
)
def price_check(token_ids: str, vs_currencies: str = "usd", tool_context=None) -> dict:
    """Get real-time crypto/token prices. Costs 1 credit per request.

    Args:
        token_ids: Comma-separated CoinGecko token IDs (e.g. "bitcoin,ethereum,solana").
        vs_currencies: Comma-separated fiat currencies (default: "usd").
    """
    return price_check_impl(token_ids, vs_currencies)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=5,
    agent_id=NVM_AGENT_ID,
)
def market_analysis(token_id: str, days: int = 7, tool_context=None) -> dict:
    """OHLCV market data with trend analysis. Costs 5 credits per request.

    Args:
        token_id: CoinGecko token ID (e.g. "bitcoin").
        days: Number of days for OHLCV data (1, 7, 14, 30, 90, 180, 365).
    """
    return market_analysis_impl(token_id, days)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=10,
    agent_id=NVM_AGENT_ID,
)
def defi_report(query: str, top_n: int = 10, tool_context=None) -> dict:
    """Full DeFi protocol report with TVL analysis. Costs 10 credits per request.

    Args:
        query: Protocol name, category, or chain to research.
        top_n: Number of top protocols to include (default: 10).
    """
    return defi_report_impl(query, top_n)


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

TOOLS = [price_check, market_analysis, defi_report]


def create_agent(model) -> Agent:
    """Create a Strands agent with payment-protected crypto tools."""
    return Agent(
        model=model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )
