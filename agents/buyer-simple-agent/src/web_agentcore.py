"""
AgentCore-aware web server for the buyer agent.

Wraps the standard web server (web.py) with AgentCore compatibility:
- Reads port from $PORT env var (set by AgentCore runtime)
- Injects AgentCorePaymentsClient so x402 tokens use the AgentCore-safe
  header prefix and survive the proxy, with SigV4 signing for outgoing requests
- Rewrites /invocations -> /api/chat (AgentCore routes all traffic there)
- Pre-registers the seller from SELLER_AGENT_ARN env var
- Includes /ping health check endpoint

Usage:
    poetry run web-agentcore
    PORT=8080 python -m src.web_agentcore
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()


def _load_secrets():
    """Load env vars from AWS Secrets Manager (hackathon/buyer-agent)."""
    secret_name = os.getenv("AWS_SECRET_NAME", "hackathon/buyer-agent")
    region = os.getenv("AWS_REGION", "us-west-2")
    try:
        import boto3
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_name)
        secrets = json.loads(resp["SecretString"])
        for key, value in secrets.items():
            if key not in os.environ:  # don't override existing env vars
                os.environ[key] = value
        print(f"Loaded {len(secrets)} env vars from Secrets Manager ({secret_name})")
    except Exception as e:
        print(f"Secrets Manager load skipped: {e}")


_load_secrets()

# Inject AgentCore-compatible PaymentsClient BEFORE importing web module
# (which imports strands_agent → purchase_a2a at module level)
from .agentcore_payments_client import (
    AgentCorePaymentsClient,
    build_agentcore_url,
)
from .tools.purchase_a2a import set_client_class

set_client_class(AgentCorePaymentsClient)

# If SELLER_AGENT_ARN is set, compute the AgentCore URL and override SELLER_A2A_URL
# BEFORE importing strands_agent (which reads it at module level)
SELLER_AGENT_ARN = os.getenv("SELLER_AGENT_ARN", "")
if SELLER_AGENT_ARN:
    region = os.getenv("AWS_REGION", "us-west-2")
    agentcore_seller_url = build_agentcore_url(SELLER_AGENT_ARN, region)
    os.environ["SELLER_A2A_URL"] = agentcore_seller_url

# Use agentcore mode: no discover_agent tool (agent card fetch doesn't work
# through AgentCore proxy), sellers are pre-registered from env vars instead
os.environ["BUYER_AGENT_MODE"] = "agentcore"

from starlette.types import ASGIApp, Receive, Scope, Send

from .log import get_logger, log
from .web import app  # noqa: E402 — must import after set_client_class & mode

_logger = get_logger("buyer.agentcore")

# AgentCore-specific env vars
PORT = int(os.getenv("PORT", "8080"))
AGENT_URL = os.getenv("AGENT_URL", f"http://localhost:{PORT}")


# ---------------------------------------------------------------------------
# Pre-register seller from env vars (agent card discovery doesn't work
# through AgentCore's proxy since GET /.well-known/agent.json isn't routed)
# ---------------------------------------------------------------------------

def _preregister_seller():
    """Pre-register the seller in the buyer's registry from env vars."""
    if not SELLER_AGENT_ARN:
        return

    from .strands_agent import seller_registry

    seller_url = os.environ.get("SELLER_A2A_URL", "")
    plan_id = os.environ.get("NVM_PLAN_ID", "")
    agent_id = os.environ.get("NVM_AGENT_ID", "")

    if not all([seller_url, plan_id, agent_id]):
        log(_logger, "REGISTRY", "SKIP",
            "SELLER_AGENT_ARN set but missing NVM_PLAN_ID or NVM_AGENT_ID")
        return

    # Build a synthetic agent card for the registry
    agent_card = {
        "name": "Data Selling Agent (AgentCore)",
        "description": "AgentCore-deployed seller agent",
        "url": seller_url,
        "skills": [],
        "capabilities": {
            "streaming": True,
            "extensions": [{
                "uri": "urn:nevermined:payment",
                "params": {
                    "planId": plan_id,
                    "agentId": agent_id,
                    "credits": 1,
                    "costDescription": "Pre-configured from env vars",
                },
            }],
        },
    }

    info = seller_registry.register(seller_url, agent_card)
    log(_logger, "REGISTRY", "PRE-REGISTERED",
        f"seller='{info.name}' url={info.url[:60]}... "
        f"plan={info.plan_id[:12]}... agent={info.agent_id[:12]}...")


_preregister_seller()


# ---------------------------------------------------------------------------
# Path rewrite middleware
# ---------------------------------------------------------------------------

class AgentCorePathMiddleware:
    """Rewrite /invocations to /api/chat for AgentCore compatibility.

    AgentCore routes all traffic to POST /invocations. This middleware
    rewrites that path to /api/chat so the buyer's chat handler receives it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/invocations":
            scope["path"] = "/api/chat"
            scope["raw_path"] = b"/api/chat"
            log(_logger, "MIDDLEWARE", "REWRITE",
                "/invocations -> /api/chat")
        await self.app(scope, receive, send)


app.add_middleware(AgentCorePathMiddleware)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Run the buyer agent web server for AgentCore."""
    import uvicorn

    log(_logger, "SERVER", "STARTUP",
        f"Buyer Agent — AgentCore Web on port {PORT}")
    log(_logger, "SERVER", "STARTUP", f"agent_url={AGENT_URL}")
    log(_logger, "SERVER", "STARTUP",
        "AgentCore PaymentsClient active (SigV4 + dual-header mode)")

    if SELLER_AGENT_ARN:
        log(_logger, "SERVER", "STARTUP",
            f"seller_arn={SELLER_AGENT_ARN}")
        log(_logger, "SERVER", "STARTUP",
            f"seller_url={os.environ.get('SELLER_A2A_URL', '')[:80]}")

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
