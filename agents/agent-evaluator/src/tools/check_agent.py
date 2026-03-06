"""Single agent health check."""

import time
import httpx


def check_agent_impl(agent_url: str) -> dict:
    """Check a single agent's health: verify its agent card, measure latency, score 0-100.

    Args:
        agent_url: Base URL of the agent (e.g. "http://localhost:9000").
    """
    agent_url = agent_url.rstrip("/")
    health_score = 0
    latency_ms = 0
    agent_name = "unknown"
    skills_count = 0
    has_payment = False
    status = "unreachable"
    details = []

    try:
        start = time.monotonic()
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{agent_url}/.well-known/agent.json")
        latency_ms = round((time.monotonic() - start) * 1000)

        if resp.status_code == 200:
            health_score += 40  # Reachable
            status = "reachable"
            details.append("Agent is reachable")

            try:
                card = resp.json()
                health_score += 20  # Valid JSON
                details.append("Valid agent card JSON")

                agent_name = card.get("name", "unknown")
                skills = card.get("skills", [])
                skills_count = len(skills)

                if skills_count > 0:
                    health_score += 15
                    details.append(f"Has {skills_count} skill(s)")

                # Check for payment extension
                capabilities = card.get("capabilities", {})
                extensions = capabilities.get("extensions", [])
                for ext in extensions:
                    if isinstance(ext, dict) and "nevermined" in str(ext.get("uri", "")):
                        has_payment = True
                        health_score += 15
                        details.append("Has Nevermined payment extension")
                        break

                if latency_ms < 2000:
                    health_score += 10
                    details.append(f"Fast response ({latency_ms}ms)")
                else:
                    details.append(f"Slow response ({latency_ms}ms)")

            except Exception:
                details.append("Invalid JSON in agent card")
                status = "invalid_card"
        else:
            details.append(f"HTTP {resp.status_code}")
            status = f"http_{resp.status_code}"

    except httpx.ConnectError:
        details.append("Connection refused")
        status = "connection_refused"
    except httpx.TimeoutException:
        details.append("Request timed out")
        status = "timeout"
    except Exception as exc:
        details.append(f"Error: {exc}")
        status = "error"

    text = (
        f"## Agent Health Check\n\n"
        f"- **Agent:** {agent_name}\n"
        f"- **URL:** {agent_url}\n"
        f"- **Status:** {status}\n"
        f"- **Latency:** {latency_ms}ms\n"
        f"- **Skills:** {skills_count}\n"
        f"- **Payment:** {'Yes' if has_payment else 'No'}\n"
        f"- **Health Score:** {health_score}/100\n\n"
        f"**Details:** {'; '.join(details)}"
    )

    return {
        "status": "success",
        "content": [{"text": text}],
        "agent_name": agent_name,
        "agent_url": agent_url,
        "latency_ms": latency_ms,
        "health_score": health_score,
        "has_payment": has_payment,
        "skills_count": skills_count,
        "check_status": status,
    }
