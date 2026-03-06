"""Multi-step agent evaluation: discover + test + rank + report."""

import os
import httpx
from openai import OpenAI

from .check_agent import check_agent_impl
from .discover_agents import discover_agents_impl


def evaluate_agents_impl(category: str = "", top_n: int = 5) -> dict:
    """Discover agents, test each one, and return a ranked evaluation report.

    Args:
        category: Category to evaluate (empty = all agents).
        top_n: Number of agents to evaluate (default: 5).
    """
    # Step 1: Discover agents
    discovery = discover_agents_impl(category=category, side="sell")
    if discovery["status"] == "error":
        return discovery

    agents = discovery.get("agents", [])
    if not agents:
        return {
            "status": "error",
            "content": [{"text": f"No agents found for category: {category or 'all'}"}],
            "report": "",
            "rankings": [],
            "agents_tested": 0,
        }

    # Step 2: Test each agent (up to top_n)
    test_agents = [a for a in agents if a.get("endpoint") and a["endpoint"].startswith("http")][:top_n]

    results = []
    for agent in test_agents:
        endpoint = agent.get("endpoint", "")
        # Extract base URL (remove path after domain)
        try:
            from urllib.parse import urlparse
            parsed = urlparse(endpoint)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            base_url = endpoint

        check = check_agent_impl(base_url)
        results.append({
            "name": agent.get("name", "unknown"),
            "team": agent.get("team", ""),
            "category": agent.get("category", ""),
            "url": base_url,
            "score": check.get("health_score", 0),
            "latency_ms": check.get("latency_ms", 0),
            "has_payment": check.get("has_payment", False),
            "skills_count": check.get("skills_count", 0),
            "status": check.get("check_status", "unknown"),
        })

    # Step 3: Rank by score, then latency
    results.sort(key=lambda r: (-r["score"], r["latency_ms"]))

    # Step 4: LLM synthesis
    data_text = f"Category filter: {category or 'all'}\nAgents tested: {len(results)}\n\n"
    for i, r in enumerate(results, 1):
        data_text += (
            f"{i}. {r['name']} (by {r['team']})\n"
            f"   Category: {r['category']} | Score: {r['score']}/100 | "
            f"Latency: {r['latency_ms']}ms | Payment: {'Yes' if r['has_payment'] else 'No'} | "
            f"Skills: {r['skills_count']} | Status: {r['status']}\n\n"
        )

    try:
        openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        completion = openai_client.chat.completions.create(
            model=os.environ.get("MODEL_ID", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an agent QA analyst. Based on the health check results, "
                        "write a brief evaluation report including:\n"
                        "1. Summary of agents tested\n"
                        "2. Ranking with scores and key metrics\n"
                        "3. Issues found (unreachable agents, missing features)\n"
                        "4. Recommendations for which agents to use\n"
                        "Keep the report under 400 words."
                    ),
                },
                {"role": "user", "content": data_text},
            ],
            max_tokens=600,
        )
        report = completion.choices[0].message.content
    except Exception as exc:
        # Fall back to raw data if LLM fails
        report = f"Agent Evaluation (LLM unavailable: {exc})\n\n{data_text}"

    return {
        "status": "success",
        "content": [{"text": report}],
        "report": report,
        "rankings": results,
        "agents_tested": len(results),
    }
