"""Plain Strands tools for A2A mode."""

from a2a.types import AgentSkill
from strands import Agent, tool

from .tools.scrape_url import scrape_url_impl
from .tools.batch_scrape import batch_scrape_impl
from .tools.deep_extract import deep_extract_impl


@tool
def scrape_url(url: str, output_format: str = "markdown") -> dict:
    """Scrape a single URL and extract clean text/markdown content. Costs 1 credit.

    Args:
        url: The URL to scrape (e.g. "https://example.com/article").
        output_format: Output format - "markdown" or "text" (default: "markdown").
    """
    return scrape_url_impl(url, output_format)


@tool
def batch_scrape(urls: str) -> dict:
    """Scrape up to 5 URLs and return structured content from all. Costs 5 credits.

    Args:
        urls: Comma-separated URLs to scrape (max 5).
    """
    return batch_scrape_impl(urls)


@tool
def deep_extract(url: str, max_pages: int = 5) -> dict:
    """Deep site extraction: crawl URL + follow links + LLM summary. Costs 10 credits.

    Args:
        url: Starting URL to deep-crawl.
        max_pages: Max pages to crawl (default: 5, max: 10).
    """
    return deep_extract_impl(url, max_pages)


ALL_TOOLS = {
    "scrape": {
        "tool": scrape_url,
        "credits": 1,
        "skill": AgentSkill(
            id="scrape_url",
            name="URL Scraper",
            description="Scrape a single URL and extract clean text/markdown. Costs 1 credit.",
            tags=["scrape", "url", "content", "extraction"],
        ),
    },
    "batch": {
        "tool": batch_scrape,
        "credits": 5,
        "skill": AgentSkill(
            id="batch_scrape",
            name="Batch Scraper",
            description="Scrape up to 5 URLs and return structured content. Costs 5 credits.",
            tags=["scrape", "batch", "multi-url"],
        ),
    },
    "deep": {
        "tool": deep_extract,
        "credits": 10,
        "skill": AgentSkill(
            id="deep_extract",
            name="Deep Extractor",
            description="Deep site crawl with link following and LLM summary. Costs 10 credits.",
            tags=["scrape", "deep", "analysis", "summary"],
        ),
    },
}


def resolve_tools(tool_names: list[str] | None = None):
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


CREDIT_MAP = {fn.__name__: e["credits"] for fn, e in
               ((ALL_TOOLS[n]["tool"], ALL_TOOLS[n]) for n in ALL_TOOLS)}
TOOLS = [ALL_TOOLS[n]["tool"] for n in ALL_TOOLS]


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


def _build_system_prompt(tools_list):
    tool_names = {t.__name__ for t in tools_list}
    lines = ["You are a web scraping and content extraction agent. You provide scraping services:\n"]
    if "scrape_url" in tool_names:
        lines.append("- **scrape_url** (1 credit) - Extract clean text/markdown from a single URL.")
    if "batch_scrape" in tool_names:
        lines.append("- **batch_scrape** (5 credits) - Scrape up to 5 URLs.")
    if "deep_extract" in tool_names:
        lines.append("- **deep_extract** (10 credits) - Deep site crawl with LLM summary.")
    lines.append("\nChoose the appropriate tool. Always include the source URL(s) in your response.")
    return "\n".join(lines)


def create_plain_agent(model, tool_names: list[str] | None = None) -> Agent:
    if tool_names:
        tools, _, _ = resolve_tools(tool_names)
        prompt = _build_system_prompt(tools)
    else:
        tools = TOOLS
        prompt = SYSTEM_PROMPT
    return Agent(model=model, tools=tools, system_prompt=prompt)
