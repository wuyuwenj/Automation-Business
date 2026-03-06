"""Plain Strands tools WITHOUT @requires_payment for A2A mode."""

from __future__ import annotations

from a2a.types import AgentSkill
from strands import Agent, tool

from .tools.ad_copy import generate_copy_impl
from .tools.branding import generate_brand_impl
from .tools.landing_page import generate_landing_page_impl


@tool
def generate_copy(
    product: str,
    audience: str = "general",
    tone: str = "professional",
) -> dict:
    """Generate ad copy assets. Costs 1 credit per request."""
    return generate_copy_impl(product, audience, tone)


@tool
def generate_brand(
    concept: str,
    industry: str = "tech",
    style: str = "modern",
) -> dict:
    """Generate a brand strategy brief. Costs 5 credits per request."""
    return generate_brand_impl(concept, industry, style)


@tool
def generate_landing_page(
    product_name: str,
    description: str,
    features: str = "",
    cta_text: str = "Get Started",
) -> dict:
    """Generate a single-file landing page. Costs 10 credits per request."""
    return generate_landing_page_impl(product_name, description, features, cta_text)


ALL_TOOLS = {
    "copy": {
        "tool": generate_copy,
        "credits": 1,
        "skill": AgentSkill(
            id="generate_copy",
            name="Ad Copy Generation",
            description="Generate headlines, taglines, CTAs, and social posts. Costs 1 credit.",
            tags=["copywriting", "ads", "marketing"],
        ),
    },
    "brand": {
        "tool": generate_brand,
        "credits": 5,
        "skill": AgentSkill(
            id="generate_brand",
            name="Brand Strategy",
            description="Generate naming, positioning, and tone guidance. Costs 5 credits.",
            tags=["branding", "strategy", "positioning"],
        ),
    },
    "landing": {
        "tool": generate_landing_page,
        "credits": 10,
        "skill": AgentSkill(
            id="generate_landing_page",
            name="Landing Page Generation",
            description="Generate complete landing page HTML. Costs 10 credits.",
            tags=["html", "landing-page", "web"],
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


CREDIT_MAP = {
    fn.__name__: entry["credits"]
    for fn, entry in ((ALL_TOOLS[name]["tool"], ALL_TOOLS[name]) for name in ALL_TOOLS)
}
TOOLS = [ALL_TOOLS[name]["tool"] for name in ALL_TOOLS]

SYSTEM_PROMPT = """\
You are a creative generation selling agent. You provide three paid services:

1. **generate_copy** (1 credit) for headlines, taglines, CTAs, and social posts.
2. **generate_brand** (5 credits) for naming, positioning, value props, and tone.
3. **generate_landing_page** (10 credits) for complete landing page HTML.

Choose the lowest-cost tool that satisfies the request and clearly explain what
creative assets were produced."""


def _build_system_prompt(tools_list):
    """Build a system prompt that only mentions the available tools."""
    tool_names = {tool_fn.__name__ for tool_fn in tools_list}
    lines = ["You are a creative generation selling agent. You provide:\n"]
    if "generate_copy" in tool_names:
        lines.append("- **generate_copy** (1 credit) for short-form marketing copy.")
    if "generate_brand" in tool_names:
        lines.append("- **generate_brand** (5 credits) for brand strategy and messaging.")
    if "generate_landing_page" in tool_names:
        lines.append("- **generate_landing_page** (10 credits) for complete landing page HTML.")
    lines.append(
        "\nChoose the lowest-cost tool that fully satisfies the user's request "
        "and clearly explain what was produced."
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
