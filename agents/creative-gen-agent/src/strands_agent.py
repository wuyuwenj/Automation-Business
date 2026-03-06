"""
Strands agent definition with Nevermined x402 payment-protected creative tools.
"""

import os

from dotenv import load_dotenv
from strands import Agent, tool

from payments_py import PaymentOptions, Payments
from payments_py.x402.strands import requires_payment

from .tools.ad_copy import generate_copy_impl
from .tools.branding import generate_brand_impl
from .tools.landing_page import generate_landing_page_impl

load_dotenv()

NVM_API_KEY = os.environ["NVM_API_KEY"]
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID = os.environ["NVM_PLAN_ID"]
NVM_AGENT_ID = os.getenv("NVM_AGENT_ID")

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=1,
    agent_id=NVM_AGENT_ID,
)
def generate_copy(
    product: str,
    audience: str = "general",
    tone: str = "professional",
    tool_context=None,
) -> dict:
    """Generate ad copy assets. Costs 1 credit per request."""
    return generate_copy_impl(product, audience, tone)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=5,
    agent_id=NVM_AGENT_ID,
)
def generate_brand(
    concept: str,
    industry: str = "tech",
    style: str = "modern",
    tool_context=None,
) -> dict:
    """Generate a brand strategy brief. Costs 5 credits per request."""
    return generate_brand_impl(concept, industry, style)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=10,
    agent_id=NVM_AGENT_ID,
)
def generate_landing_page(
    product_name: str,
    description: str,
    features: str = "",
    cta_text: str = "Get Started",
    tool_context=None,
) -> dict:
    """Generate a single-file landing page. Costs 10 credits per request."""
    return generate_landing_page_impl(product_name, description, features, cta_text)


SYSTEM_PROMPT = """\
You are a creative generation selling agent. You provide three paid services:

1. **generate_copy** (1 credit) for short-form marketing copy such as headlines,
   taglines, CTAs, and social posts.
2. **generate_brand** (5 credits) for naming, positioning, value propositions,
   and tone guidance.
3. **generate_landing_page** (10 credits) for a complete landing page HTML file.

Choose the lowest-cost tool that fully satisfies the user's request. Use
generate_copy for campaign-ready messaging, generate_brand for strategic brand
direction, and generate_landing_page when the user wants page copy plus a ready
to-save HTML landing page.

Be explicit about which asset bundle was produced and keep the response concise."""

TOOLS = [generate_copy, generate_brand, generate_landing_page]


def create_agent(model) -> Agent:
    """Create a Strands agent with the given model."""
    return Agent(
        model=model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )
