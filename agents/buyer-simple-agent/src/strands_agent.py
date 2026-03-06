"""
Strands agent definition with smart buyer tools for x402 data purchasing.

This is the heart of the buyer kit. Both agent.py (interactive CLI) and
agent_agentcore.py (AWS) import from here. The tools are plain @tool —
NOT @requires_payment — because the buyer generates tokens, not receives them.

Smart buyer features:
- Marketplace discovery via Nevermined Discovery API
- Pre-purchase seller filtering by keyword/category
- Explore/exploit seller selection with ROI tracking
- Post-purchase evaluation with structured rubric
- Mindra workflow orchestration for multi-seller queries

Usage:
    from src.strands_agent import payments, create_agent, NVM_PLAN_ID, seller_registry
"""

import os
import urllib3

from dotenv import load_dotenv
from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager

from payments_py import Payments, PaymentOptions

from .budget import Budget
from .ledger import PurchaseLedger
from .log import get_logger, log
from .payment_diagnostics import diagnose_error
from .registry import SellerRegistry
from .tools.balance import check_balance_impl
from .tools.discover import discover_pricing_impl
from .tools.discover_a2a import discover_agent_impl
from .tools.discover_marketplace import discover_marketplace_impl
from .tools.evaluate import evaluate_purchase_impl
from .tools.filter_sellers import filter_sellers_impl
from .tools.orchestrate import run_workflow_impl
from .tools.purchase import purchase_data_impl
from .tools.purchase_a2a import purchase_a2a_impl, purchase_http_impl
from .tools.select_seller import select_seller_impl, _sort_candidates

load_dotenv()

# Suppress urllib3 warnings from payments_py SDK (sandbox uses verify=False)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NVM_API_KEY = os.environ["NVM_API_KEY"]
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID = os.environ["NVM_PLAN_ID"]
NVM_AGENT_ID = os.getenv("NVM_AGENT_ID")
SELLER_URL = os.getenv("SELLER_URL", "http://localhost:3000")
SELLER_A2A_URL = os.getenv("SELLER_A2A_URL", "")

MAX_DAILY_SPEND = int(os.getenv("MAX_DAILY_SPEND", "0"))
MAX_PER_REQUEST = int(os.getenv("MAX_PER_REQUEST", "0"))

MINDRA_API_KEY = os.getenv("MINDRA_API_KEY", "")
MINDRA_WORKFLOW_SLUG = os.getenv("MINDRA_WORKFLOW_SLUG", "basic-search-agent")

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)

budget = Budget(max_daily=MAX_DAILY_SPEND, max_per_request=MAX_PER_REQUEST)
ledger = PurchaseLedger()

_logger = get_logger("buyer.tools")

# Shared seller registry — used by tools and registration server
seller_registry = SellerRegistry()

# Track sellers that failed during purchase (so select_seller can skip them)
_failed_sellers: set[str] = set()


def _pick_alternate_seller(exclude_urls: set[str]) -> dict | None:
    """Pick one alternate seller for an automatic retry."""
    candidates = [
        s for s in seller_registry.list_all()
        if s["url"] not in exclude_urls
    ]
    if not candidates:
        return None

    # Prefer a known-good seller from prior successful purchases.
    best_global_url = ledger.get_best_seller_url()
    if best_global_url:
        best_global = next(
            (s for s in candidates if s["url"] == best_global_url),
            None,
        )
        if best_global:
            return best_global

    return _sort_candidates(candidates)[0]


def _prepend_result_note(result: dict, message: str) -> dict:
    """Add retry context to the tool's text output."""
    content = result.get("content") or []
    if content and isinstance(content[0], dict):
        existing = content[0].get("text", "")
        content[0]["text"] = f"{message}\n\n{existing}" if existing else message
    else:
        result["content"] = [{"text": message}]
    return result


def _purchase_a2a_once(query: str, url: str) -> dict:
    """Execute a single purchase attempt against one seller."""
    log(_logger, "TOOLS", "PURCHASE", f'url={url} query="{query[:60]}"')

    # Check registry for cached payment info (skip discovery round-trip)
    cached = seller_registry.get_payment_info(url)
    if cached and cached["planId"]:
        log(_logger, "TOOLS", "PURCHASE",
            f'using cached payment info plan={cached["planId"][:12]}')
        plan_id = cached["planId"]
        # Only fall back to NVM_AGENT_ID when using our own NVM_PLAN_ID.
        # Using NVM_AGENT_ID with another seller's plan causes
        # "plan is not associated to the agent" errors.
        if plan_id == NVM_PLAN_ID:
            agent_id = cached["agentId"] or NVM_AGENT_ID or ""
        else:
            agent_id = cached["agentId"] or ""
        min_credits = cached["credits"]
    else:
        # Fall back to full discovery via A2A agent card
        discovery = discover_agent_impl(url)
        if discovery.get("status") != "success":
            # If A2A discovery fails, use NVM_PLAN_ID as last resort
            log(_logger, "TOOLS", "PURCHASE",
                "A2A discovery failed, falling back to NVM_PLAN_ID")
            plan_id = NVM_PLAN_ID
            agent_id = NVM_AGENT_ID or ""
            min_credits = 1
        else:
            payment = discovery.get("payment", {})
            plan_id = payment.get("planId", NVM_PLAN_ID)
            # Same logic: only use NVM_AGENT_ID for our own plan
            discovered_plan = payment.get("planId", "")
            if discovered_plan and discovered_plan != NVM_PLAN_ID:
                agent_id = payment.get("agentId", "")
            else:
                agent_id = payment.get("agentId", NVM_AGENT_ID or "")
            min_credits = payment.get("credits", 1)

    if not plan_id:
        return {
            "status": "error",
            "content": [{"text": "No plan ID found in agent card or environment."}],
            "credits_used": 0,
        }

    # Auto-subscribe: check if we're subscribed, order plan if not.
    # If ordering the primary plan fails (e.g. requires USDC), try alternative plans.
    all_plan_ids = cached.get("allPlanIds", []) if cached else []
    try:
        balance_result = payments.plans.get_plan_balance(plan_id)
        if not balance_result.is_subscriber:
            log(_logger, "TOOLS", "PURCHASE",
                f"Not subscribed to plan {plan_id[:12]}... auto-ordering")
            try:
                payments.plans.order_plan(plan_id)
                log(_logger, "TOOLS", "PURCHASE", "Plan ordered successfully")
            except Exception as order_err:
                log(_logger, "TOOLS", "PURCHASE",
                    f"Order failed for primary plan: {order_err}")
                diagnosis = diagnose_error(str(order_err))
                if diagnosis:
                    log(_logger, "TOOLS", "DIAG", diagnosis)
                # Try alternative plans (different payment scheme)
                ordered = False
                for alt_plan in all_plan_ids:
                    if alt_plan == plan_id:
                        continue
                    try:
                        log(_logger, "TOOLS", "PURCHASE",
                            f"Trying alternative plan {alt_plan[:12]}...")
                        payments.plans.order_plan(alt_plan)
                        log(_logger, "TOOLS", "PURCHASE",
                            f"Alternative plan ordered! Switching to {alt_plan[:12]}")
                        plan_id = alt_plan
                        ordered = True
                        break
                    except Exception:
                        continue
                if not ordered:
                    log(_logger, "TOOLS", "PURCHASE",
                        "All plan orders failed (continuing with token anyway)")
    except Exception as e:
        log(_logger, "TOOLS", "PURCHASE",
            f"Balance/order check failed (continuing anyway): {e}")
        diagnosis = diagnose_error(str(e))
        if diagnosis:
            log(_logger, "TOOLS", "DIAG", diagnosis)

    # Budget pre-check
    allowed, reason = budget.can_spend(min_credits)
    if not allowed:
        return {
            "status": "budget_exceeded",
            "content": [{"text": f"Budget check failed: {reason}"}],
            "credits_used": 0,
        }

    # Try A2A first, fall back to direct HTTP if A2A fails (400/404)
    result = purchase_a2a_impl(
        payments=payments,
        plan_id=plan_id,
        agent_url=url,
        agent_id=agent_id,
        query=query,
    )

    # If A2A fails with 400/404 (not a2a endpoint), try direct HTTP
    if result.get("status") == "error":
        error_text = result.get("content", [{}])[0].get("text", "") if result.get("content") else ""
        if any(code in error_text for code in ("400", "402", "404", "405", "307", "Redirect")):
            log(_logger, "TOOLS", "PURCHASE",
                "A2A failed, falling back to direct HTTP with x402")
            result = purchase_http_impl(
                payments=payments,
                plan_id=plan_id,
                agent_url=url,
                agent_id=agent_id,
                query=query,
            )

    log(_logger, "TOOLS", "PURCHASE",
        f'url={url} status={result.get("status")} credits={result.get("credits_used", 0)}')
    return result


# ---------------------------------------------------------------------------
# Original buyer tools (plain @tool — no @requires_payment)
# ---------------------------------------------------------------------------

@tool
def discover_pricing(seller_url: str = "") -> dict:
    """Discover a seller's available data services and pricing tiers.

    Call this first to understand what data is available and how much it costs.

    Args:
        seller_url: Base URL of the seller (defaults to SELLER_URL env var).
    """
    url = seller_url or SELLER_URL
    return discover_pricing_impl(url)


@tool
def check_balance() -> dict:
    """Check your Nevermined credit balance and daily budget status.

    Returns your remaining credits on the seller's plan and your
    local spending budget status.
    """
    log(_logger, "TOOLS", "BALANCE", f"plan={NVM_PLAN_ID[:12]}")
    result = check_balance_impl(payments, NVM_PLAN_ID)
    budget_status = budget.get_status()
    result["budget"] = budget_status

    budget_line = (
        f" | Budget: remaining={budget_status['daily_remaining']} "
        f"spent={budget_status['daily_spent']} limit={budget_status['daily_limit']}"
    )
    if result.get("content"):
        result["content"][0]["text"] += budget_line

    return result


@tool
def purchase_data(query: str, seller_url: str = "") -> dict:
    """Purchase data from a seller using x402 payment (FINAL STEP).

    Generates an x402 access token and sends the query to the seller.
    Budget limits are checked before purchasing.

    IMPORTANT: Call this tool AT MOST ONCE per user request. After it returns
    (success or error), stop calling tools and report the result to the user.

    Args:
        query: The data query to send to the seller.
        seller_url: Base URL of the seller (defaults to SELLER_URL env var).
    """
    url = seller_url or SELLER_URL

    # Pre-check with minimum 1 credit (actual cost is determined by the seller)
    allowed, reason = budget.can_spend(1)
    if not allowed:
        return {
            "status": "budget_exceeded",
            "content": [{"text": f"Budget check failed: {reason}"}],
            "credits_used": 0,
        }

    result = purchase_data_impl(
        payments=payments,
        plan_id=NVM_PLAN_ID,
        seller_url=url,
        query=query,
        agent_id=NVM_AGENT_ID,
    )

    credits_used = result.get("credits_used", 0)
    if result.get("status") == "success" and credits_used > 0:
        budget.record_purchase(credits_used, url, query)

    return result


# ---------------------------------------------------------------------------
# A2A buyer tools
# ---------------------------------------------------------------------------

@tool
def list_sellers() -> dict:
    """List all registered sellers, their skills, and pricing.

    Sellers register automatically via A2A when they start with --buyer-url.
    You can also register sellers manually with discover_agent, or load them
    from the marketplace with discover_marketplace.
    """
    sellers = seller_registry.list_all()
    log(_logger, "TOOLS", "LIST_SELLERS", f"count={len(sellers)}")
    if not sellers:
        return {
            "status": "success",
            "content": [{"text": "No sellers registered yet. "
                         "Use discover_marketplace to find sellers from the hackathon, "
                         "or discover_agent to add one by URL."}],
            "sellers": [],
        }

    lines = [f"Sellers ({len(sellers)}):"]
    for s in sellers:
        free = " FREE" if s.get("has_free_plan") else ""
        lines.append(f"  - {s['name']} [{s.get('category','?')}] {s['credits']}cr{free} ({s['url']})")

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "sellers": sellers,
    }


@tool
def discover_agent(agent_url: str = "") -> dict:
    """Discover a seller via A2A protocol by fetching its agent card.

    Retrieves /.well-known/agent.json from the seller and parses
    the payment extension to find plan ID, agent ID, and pricing.
    Also registers the seller in the local registry.

    Args:
        agent_url: Base URL of the A2A agent (defaults to SELLER_A2A_URL env var).
    """
    url = agent_url or SELLER_A2A_URL
    log(_logger, "TOOLS", "DISCOVER", f"url={url}")
    result = discover_agent_impl(url)

    if result.get("status") == "success":
        log(_logger, "TOOLS", "DISCOVER",
            f'found name={result.get("name", "?")} skills={len(result.get("skills", []))}')

        # Also register in the seller registry (best-effort)
        import httpx
        try:
            card_url = f"{url.rstrip('/')}/.well-known/agent.json"
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(card_url)
            if resp.status_code == 200:
                seller_registry.register(url, resp.json())
        except Exception:
            pass

    return result


@tool
def purchase_a2a(query: str, agent_url: str = "") -> dict:
    """Buy data from a seller via A2A with x402 payment. Call AT MOST ONCE.

    Args:
        query: The data query to send.
        agent_url: Seller URL from select_seller.
    """
    url = agent_url or SELLER_A2A_URL

    # If no URL specified, try the registry
    if not url:
        url = seller_registry.get_first_url()
    if not url:
        return {
            "status": "error",
            "content": [{"text": "No seller URL provided and no sellers registered. "
                         "Use list_sellers to check, or provide an agent_url."}],
            "credits_used": 0,
        }

    result = _purchase_a2a_once(query, url)
    credits_used = result.get("credits_used", 0)
    if result.get("status") == "success" and credits_used > 0:
        budget.record_purchase(credits_used, url, query)
        return result

    if result.get("status") == "error":
        # Track failed sellers so select_seller can skip them
        _failed_sellers.add(url)
        log(_logger, "TOOLS", "PURCHASE",
            f"Marked seller as failed: {url} (total failed: {len(_failed_sellers)})")

        alternate = _pick_alternate_seller(_failed_sellers | {url})
        if alternate:
            alt_url = alternate["url"]
            alt_name = alternate["name"]
            log(_logger, "TOOLS", "PURCHASE",
                f"Retrying once with alternate seller {alt_name} @ {alt_url}")
            retry_result = _purchase_a2a_once(query, alt_url)
            retry_credits = retry_result.get("credits_used", 0)
            if retry_result.get("status") == "success" and retry_credits > 0:
                budget.record_purchase(retry_credits, alt_url, query)
                return _prepend_result_note(
                    retry_result,
                    f"Initial seller failed at {url}. Retried once with '{alt_name}' ({alt_url}) and succeeded.",
                )

            if retry_result.get("status") == "error":
                _failed_sellers.add(alt_url)
                log(_logger, "TOOLS", "PURCHASE",
                    f"Marked alternate seller as failed: {alt_url} (total failed: {len(_failed_sellers)})")
                return _prepend_result_note(
                    retry_result,
                    f"Initial seller failed at {url}. Automatic fallback to '{alt_name}' ({alt_url}) also failed.",
                )

            return _prepend_result_note(
                retry_result,
                f"Initial seller failed at {url}. Automatic fallback used '{alt_name}' ({alt_url}).",
            )

    return result


# ---------------------------------------------------------------------------
# Smart buyer tools
# ---------------------------------------------------------------------------

@tool
def discover_marketplace(category: str = "") -> dict:
    """Load sellers from the marketplace into the registry.

    Args:
        category: Optional category filter.
    """
    return discover_marketplace_impl(NVM_API_KEY, seller_registry, category)


@tool
def filter_sellers(query: str) -> dict:
    """Find the most relevant sellers for a query (FREE — no credits spent).

    Ranks sellers by keyword overlap, category match, and description
    relevance using metadata already in the registry.

    Args:
        query: The user's query to match against seller capabilities.
    """
    return filter_sellers_impl(query, seller_registry)


@tool
def select_seller(query: str, query_category: str) -> dict:
    """Pick the best seller for this query using explore/exploit logic.

    Args:
        query: The user's query.
        query_category: e.g. "research", "defi", "analysis", "data".
    """
    return select_seller_impl(query, query_category, seller_registry, ledger, _failed_sellers)


@tool
def evaluate_purchase(
    query: str,
    query_category: str,
    seller_name: str,
    seller_url: str,
    response_text: str,
    credits_spent: int,
    relevance: int,
    depth: int,
    actionability: int,
    specificity: int,
    reasoning: str,
) -> dict:
    """Score a purchase response (0-2 each: relevance, depth, actionability, specificity)."""
    return evaluate_purchase_impl(
        ledger=ledger,
        query=query,
        query_category=query_category,
        seller_url=seller_url,
        seller_name=seller_name,
        response_text=response_text,
        credits_spent=credits_spent,
        relevance=relevance,
        depth=depth,
        actionability=actionability,
        specificity=specificity,
        reasoning=reasoning,
    )


@tool
def run_research_workflow(query: str) -> dict:
    """Run a multi-step research workflow using Mindra orchestration.

    Triggers a Mindra workflow that coordinates queries across multiple
    sources and returns synthesized results. Use this for complex queries
    that benefit from multi-source research.

    Args:
        query: The research query to investigate.
    """
    return run_workflow_impl(
        mindra_api_key=MINDRA_API_KEY,
        workflow_slug=MINDRA_WORKFLOW_SLUG,
        query=query,
    )


@tool
def get_purchase_history() -> dict:
    """Get past purchases, ROI, and seller rankings."""
    summary = ledger.get_summary()
    log(_logger, "TOOLS", "HISTORY",
        f"total={summary['total_purchases']} avg_roi={summary['avg_roi']}")

    if summary["total_purchases"] == 0:
        return {
            "status": "success",
            "content": [{"text": "No purchases recorded yet."}],
            "summary": summary,
        }

    lines = [
        f"Purchase History Summary:",
        f"  Total purchases: {summary['total_purchases']}",
        f"  Total spent: {summary['total_spent']} credits",
        f"  Average ROI: {summary['avg_roi']:.1f}",
        "",
        "By Seller:",
    ]
    for url, stats in summary["by_seller"].items():
        lines.append(
            f"  {stats['name']}: {stats['purchases']} purchases, "
            f"avg quality {stats['avg_quality']}/8, avg ROI {stats['avg_roi']:.1f}, "
            f"total spent {stats['total_spent']} credits"
        )

    lines.append("\nBy Category:")
    for cat, stats in summary["by_category"].items():
        lines.append(
            f"  {cat}: {stats['purchases']} purchases, "
            f"{stats['sellers_tried']} sellers tried, avg ROI {stats['avg_roi']:.1f}"
        )

    lines.append("\nRecent Purchases:")
    for r in summary["recent"]:
        eval_data = r.get("evaluation", {})
        lines.append(
            f"  [{r['timestamp'][:19]}] {r['seller_name']}: "
            f"quality={r['quality_score']}/8 roi={r['roi']:.1f} "
            f"cost={r['cost']} — \"{r['query'][:50]}\""
        )
        if eval_data.get("reasoning"):
            lines.append(f"    Reasoning: {eval_data['reasoning'][:100]}")

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

_GUIDELINES = """
Rules: call purchase AT MOST ONCE. After it returns, STOP and report results. \
Do not call purchase a second time. Budget exceeded → explain and stop."""

_SMART_BUYER_PROMPT = """\
You buy data from marketplace sellers. Steps (in order, each once):
1. discover_marketplace — load sellers (once per session).
2. select_seller — picks best seller (pass query_category like "research","defi","analysis").
   MUST use the URL it returns.
3. purchase_a2a — buy from that exact URL.
4. evaluate_purchase — score response (relevance/depth/actionability/specificity, 0-2 each).
Report: data received, seller chosen, quality score, credits spent.
""" + _GUIDELINES

_A2A_PROMPT = """\
You are a data buying agent. You help users discover and purchase data from \
sellers using the A2A (Agent-to-Agent) protocol with Nevermined payments.

Sellers register with you automatically when they start. Use list_sellers \
to see available sellers, their skills, and pricing.

Your workflow (do each step once, in order):
1. **list_sellers** — See all registered sellers and their capabilities.
2. **discover_agent** — Manually discover a seller by URL (also registers it).
3. **check_balance** — Check your credit balance and budget.
4. **purchase_a2a** — Send an A2A message with automatic payment (FINAL STEP).

After step 4 completes, you are DONE. Report the results and stop.
""" + _GUIDELINES

_AGENTCORE_PROMPT = """\
You buy data from marketplace sellers. Steps (in order, each once):
1. discover_marketplace — load sellers (once per session, skip if already loaded).
2. filter_sellers — find relevant sellers for the query.
3. check_balance — verify credits.
4. purchase_a2a — buy from the best match (FINAL STEP).
After step 4, STOP and report results. Do NOT discover agents by URL — \
agent card discovery is unavailable in this environment.
""" + _GUIDELINES

_HTTP_PROMPT = """\
You are a data buying agent. You help users discover and purchase data from \
sellers using the x402 HTTP payment protocol.

Your workflow (do each step once, in order):
1. **discover_pricing** — Call this first to see what the seller offers.
2. **check_balance** — Check your credit balance and budget before purchasing.
3. **purchase_data** — Buy data by sending an x402-protected HTTP request (FINAL STEP).

After step 3 completes, you are DONE. Report the results and stop.
""" + _GUIDELINES

# Tool sets for each mode
_SMART_TOOLS = [
    discover_marketplace,
    select_seller,
    purchase_a2a,
    evaluate_purchase,
    get_purchase_history,
]

_A2A_TOOLS = [list_sellers, discover_agent, check_balance, purchase_a2a]
_AGENTCORE_TOOLS = [discover_marketplace, filter_sellers, list_sellers, check_balance, purchase_a2a]
_HTTP_TOOLS = [discover_pricing, check_balance, purchase_data]


def create_agent(model, mode: str = "a2a") -> Agent:
    """Create a Strands agent with the given model.

    Args:
        model: A Strands-compatible model (OpenAIModel, BedrockModel, etc.)
        mode: Agent mode —
              "smart" for smart buyer with evaluation/ROI (default for hackathon),
              "a2a" for basic A2A marketplace tools,
              "http" for direct x402 HTTP tools,
              "agentcore" for AgentCore deployment (no discover_agent).

    Returns:
        Configured Strands Agent with buyer tools.
    """
    if mode == "smart":
        tools = _SMART_TOOLS
        prompt = _SMART_BUYER_PROMPT
    elif mode == "a2a":
        tools = _A2A_TOOLS
        prompt = _A2A_PROMPT
    elif mode == "agentcore":
        tools = _AGENTCORE_TOOLS
        prompt = _AGENTCORE_PROMPT
    elif mode == "http":
        tools = _HTTP_TOOLS
        prompt = _HTTP_PROMPT
    else:
        raise ValueError(f"Invalid mode {mode!r}, must be 'smart', 'a2a', 'agentcore', or 'http'")
    return Agent(
        model=model,
        tools=tools,
        system_prompt=prompt,
        conversation_manager=SlidingWindowConversationManager(
            window_size=14,
            should_truncate_results=True,
            per_turn=True,
        ),
    )
