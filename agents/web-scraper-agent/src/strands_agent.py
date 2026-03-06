"""
Strands agent with Nevermined x402 payment-protected tools.

Used by agent.py (FastAPI HTTP mode). Tools use @requires_payment decorator.
"""

import os

from dotenv import load_dotenv
from strands import Agent, tool

from payments_py import Payments, PaymentOptions
from payments_py.x402.strands import requires_payment

from .tools.scrape_url import scrape_url_impl
from .tools.batch_scrape import batch_scrape_impl
from .tools.deep_extract import deep_extract_impl

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
def scrape_url(url: str, output_format: str = "markdown", tool_context=None) -> dict:
    """Scrape a single URL and extract clean text/markdown content. Costs 1 credit.

    Args:
        url: The URL to scrape (e.g. "https://example.com/article").
        output_format: Output format - "markdown" or "text" (default: "markdown").
    """
    return scrape_url_impl(url, output_format)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=5,
    agent_id=NVM_AGENT_ID,
)
def batch_scrape(urls: str, tool_context=None) -> dict:
    """Scrape up to 5 URLs and return structured content from all. Costs 5 credits.

    Args:
        urls: Comma-separated URLs to scrape (max 5).
    """
    return batch_scrape_impl(urls)


@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=10,
    agent_id=NVM_AGENT_ID,
)
def deep_extract(url: str, max_pages: int = 5, tool_context=None) -> dict:
    """Deep site extraction: crawl URL + follow links + LLM summary. Costs 10 credits.

    Args:
        url: Starting URL to deep-crawl.
        max_pages: Max pages to crawl (default: 5, max: 10).
    """
    return deep_extract_impl(url, max_pages)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a web scraping and content extraction agent. You provide web scraping \
services at three pricing tiers:

1. **scrape_url** (1 credit) - Extract clean text/markdown from a single URL.
2. **batch_scrape** (5 credits) - Scrape up to 5 URLs and return structured content.
3. **deep_extract** (10 credits) - Deep site analysis: crawl a URL, follow internal \
links, and generate an LLM summary.

Choose the appropriate tool based on the user's request. For a single page, use \
scrape_url. For multiple URLs, use batch_scrape. For comprehensive site analysis, \
use deep_extract.

Always include the source URL(s) in your response."""

TOOLS = [scrape_url, batch_scrape, deep_extract]


def create_agent(model) -> Agent:
    """Create a Strands agent with payment-protected scraper tools."""
    return Agent(
        model=model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )
