"""
Plain Strands tools WITHOUT @requires_payment -- for A2A mode.

In A2A mode, payment validation and credit settlement happen at the A2A
message level via PaymentsRequestHandler. Individual tools don't need
the @requires_payment decorator.
"""

from a2a.types import AgentSkill
from strands import Agent, tool

from .tools.check_agent import check_agent_impl
from .tools.discover_agents import discover_agents_impl
from .tools.evaluate_agents import evaluate_agents_impl


# ---------------------------------------------------------------------------
# Plain Strands tools (no payment decorator)
# ---------------------------------------------------------------------------

@tool
def check_agent(agent_url: str) -> dict:
    """Check a single agent's health: verify its agent card, measure latency, score 0-100. Costs 1 credit per request.

    Args:
        agent_url: Base URL of the agent (e.g. "http://localhost:9000").
    """
    return check_agent_impl(agent_url)


@tool
def discover_agents(category: str = "", side: str = "sell") -> dict:
    """Query the hackathon Discovery API for registered agents. Costs 3 credits per request.

    Args:
        category: Filter by category (e.g. "DeFi", "AI/ML", "Infrastructure").
        side: Filter by side - "sell", "buy", or "" for both.
    """
    return discover_agents_impl(category, side)


@tool
def evaluate_agents(category: str = "", top_n: int = 5) -> dict:
    """Discover agents, test each one, and return a ranked evaluation report. Costs 10 credits per request.

    Args:
        category: Category to evaluate (empty = all agents).
        top_n: Number of agents to evaluate (default: 5).
    """
    return evaluate_agents_impl(category, top_n)


# ---------------------------------------------------------------------------
# ALL_TOOLS registry
# ---------------------------------------------------------------------------

ALL_TOOLS = {
    "check": {
        "tool": check_agent,
        "credits": 1,
        "skill": AgentSkill(
            id="check_agent",
            name="Agent Health Check",
            description="Check a single agent's health, latency, and agent card validity. Costs 1 credit.",
            tags=["agent", "health", "check", "latency"],
        ),
    },
    "discover": {
        "tool": discover_agents,
        "credits": 3,
        "skill": AgentSkill(
            id="discover_agents",
            name="Discover Agents",
            description="Query the hackathon Discovery API for registered agents. Costs 3 credits.",
            tags=["agent", "discover", "marketplace", "registry"],
        ),
    },
    "evaluate": {
        "tool": evaluate_agents,
        "credits": 10,
        "skill": AgentSkill(
            id="evaluate_agents",
            name="Agent Evaluation Report",
            description="Discover agents, test each one, and produce a ranked evaluation report. Costs 10 credits.",
            tags=["agent", "evaluate", "report", "ranking"],
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


def _build_system_prompt(tools_list):
    """Build a system prompt that only mentions the available tools."""
    tool_names = {t.__name__ for t in tools_list}
    lines = ["You are an agent QA and health monitoring agent. You help users assess the reliability and availability of other AI agents in the marketplace.\n"]
    if "check_agent" in tool_names:
        lines.append("- **check_agent** (1 credit) - Check a single agent's health and latency.")
    if "discover_agents" in tool_names:
        lines.append("- **discover_agents** (3 credits) - Query the Discovery API for available agents.")
    if "evaluate_agents" in tool_names:
        lines.append("- **evaluate_agents** (10 credits) - Discover agents, test each one, and produce a ranked evaluation report.")
    lines.append(
        "\nChoose the appropriate tool based on the user's request. "
        "Always report specific metrics (latency, health score) and be objective in assessments."
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
