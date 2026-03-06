"""
Strands agent with Nevermined x402 payment-protected tools.

Used by agent.py (FastAPI HTTP mode). Tools use @requires_payment decorator.
"""

import os

from dotenv import load_dotenv
from strands import Agent, tool

from payments_py import Payments, PaymentOptions
from payments_py.x402.strands import requires_payment

from .tools.check_agent import check_agent_impl
from .tools.discover_agents import discover_agents_impl
from .tools.evaluate_agents import evaluate_agents_impl

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
def check_agent(agent_url: str, tool_context=None) -> dict:
    """Check a single agent's health. Costs 1 credit per request.

    Args:
        agent_url: Base URL of the agent (e.g. "http://localhost:9000").
    """
    return check_agent_impl(agent_url)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=3,
    agent_id=NVM_AGENT_ID,
)
def discover_agents(category: str = "", side: str = "sell", tool_context=None) -> dict:
    """Query the Discovery API for registered agents. Costs 3 credits per request.

    Args:
        category: Filter by category (e.g. "DeFi", "AI/ML", "Infrastructure").
        side: Filter by side - "sell", "buy", or "" for both.
    """
    return discover_agents_impl(category, side)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=10,
    agent_id=NVM_AGENT_ID,
)
def evaluate_agents(category: str = "", top_n: int = 5, tool_context=None) -> dict:
    """Full agent evaluation report. Costs 10 credits per request.

    Args:
        category: Category to evaluate (empty = all agents).
        top_n: Number of agents to evaluate (default: 5).
    """
    return evaluate_agents_impl(category, top_n)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an agent QA and health monitoring agent. You help users assess the \
reliability and availability of other AI agents in the marketplace.

1. **check_agent** (1 credit) - Check a single agent's health and latency.
2. **discover_agents** (3 credits) - Query the Discovery API for available agents.
3. **evaluate_agents** (10 credits) - Discover agents, test each one, and produce \
a ranked evaluation report.

Choose the appropriate tool based on the user's request. For checking a specific \
agent, use check_agent. For finding agents, use discover_agents. For comprehensive \
evaluation, use evaluate_agents.

Always report specific metrics (latency, health score) and be objective in assessments."""

TOOLS = [check_agent, discover_agents, evaluate_agents]


def create_agent(model) -> Agent:
    """Create a Strands agent with payment-protected evaluator tools."""
    return Agent(
        model=model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )
