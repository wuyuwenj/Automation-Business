"""
AgentCore-aware A2A server entry point for the creative generation agent.

Wraps the standard A2A server (agent_a2a.py) with AgentCore compatibility:
- Reads port from $PORT env var (set by AgentCore runtime)
- Sets agent card URL from $AGENT_URL env var (AgentCore public URL)
- Adds header remapping middleware so `payment-signature` survives the proxy
- Includes /ping health check endpoint

AgentCore's proxy strips custom HTTP headers. Only headers prefixed with
`X-Amzn-Bedrock-AgentCore-Runtime-Custom-` pass through. The middleware
below copies the prefixed header to `payment-signature` before the
PaymentsA2AServer middleware sees it.

Usage:
    poetry run agent-a2a-agentcore
    PORT=8080 AGENT_URL=https://my-agent.agentcore.aws python -m src.agent_a2a_agentcore
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
from strands.models.openai import OpenAIModel

from payments_py import Payments, PaymentOptions
from payments_py.a2a.agent_card import build_payment_agent_card
from payments_py.a2a.server import PaymentsA2AServer

from .agent_a2a import StrandsA2AExecutor
from .log import get_logger, log
from .strands_agent_plain import create_plain_agent, resolve_tools

load_dotenv()

NVM_API_KEY = os.environ["NVM_API_KEY"]
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID = os.environ["NVM_PLAN_ID"]
NVM_AGENT_ID = os.getenv("NVM_AGENT_ID", "")

# AgentCore-specific env vars
PORT = int(os.getenv("PORT", "8080"))
AGENT_URL = os.getenv("AGENT_URL", f"http://localhost:{PORT}")

_logger = get_logger("seller.agentcore")

AGENTCORE_HEADER = b"x-amzn-bedrock-agentcore-runtime-custom-payment-signature"

if not NVM_AGENT_ID:
    log(_logger, "SERVER", "ERROR",
        "NVM_AGENT_ID is required for A2A mode. "
        "Set it in your .env file (find it in the Nevermined App agent settings).")
    sys.exit(1)

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)


# ---------------------------------------------------------------------------
# Header remapping middleware
# ---------------------------------------------------------------------------

class AgentCoreHeaderMiddleware:
    """ASGI middleware that remaps AgentCore custom headers to payment-signature.

    AgentCore strips all custom headers except those prefixed with
    X-Amzn-Bedrock-AgentCore-Runtime-Custom-. This middleware copies
    the prefixed payment header into the standard `payment-signature`
    header so downstream middleware (PaymentsA2AServer) can read it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            # AgentCore routes all traffic to /invocations; rewrite to /
            # so the A2A JSON-RPC handler (registered at POST /) receives it.
            if scope.get("path") == "/invocations":
                scope["path"] = "/"
                scope["raw_path"] = b"/"

            headers = list(scope.get("headers", []))
            has_payment_sig = any(k == b"payment-signature" for k, _ in headers)

            if not has_payment_sig:
                for key, value in headers:
                    if key == AGENTCORE_HEADER:
                        headers.append((b"payment-signature", value))
                        log(_logger, "MIDDLEWARE", "REMAP",
                            "copied AgentCore custom header -> payment-signature")
                        break
                scope["headers"] = headers

        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Start the A2A server with AgentCore compatibility."""
    # Resolve tools (default: all)
    tools_list, credit_map, skills = resolve_tools(None)

    cost_parts = [f"{name}={cost}" for name, cost in credit_map.items()]
    cost_description = "Credits vary by tool: " + ", ".join(cost_parts)

    # Build agent card with the AgentCore public URL
    base_agent_card = {
        "name": "Creative Generation Selling Agent",
        "description": (
            "AI-powered creative agent that provides ad copy, brand strategy, "
            "and landing page generation with tiered pricing."
        ),
        "url": AGENT_URL,
        "version": "0.1.0",
        "skills": [s.model_dump() for s in skills],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
        },
    }

    agent_card = build_payment_agent_card(
        base_agent_card,
        {
            "paymentType": "dynamic",
            "credits": min(credit_map.values()),
            "planId": NVM_PLAN_ID,
            "agentId": NVM_AGENT_ID,
            "costDescription": cost_description,
        },
    )

    # Create strands agent and executor
    model = OpenAIModel(
        client_args={"api_key": os.environ.get("OPENAI_API_KEY", "")},
        model_id=os.getenv("MODEL_ID", "gpt-4o-mini"),
    )

    agent = create_plain_agent(model, None)
    executor = StrandsA2AExecutor(agent, credit_map)

    log(_logger, "SERVER", "STARTUP",
        f"Creative Generation Selling Agent — AgentCore A2A on port {PORT}")
    log(_logger, "SERVER", "STARTUP", f"agent_url={AGENT_URL}")
    log(_logger, "SERVER", "STARTUP",
        f"plan={NVM_PLAN_ID} agent={NVM_AGENT_ID} env={NVM_ENVIRONMENT}")
    log(_logger, "SERVER", "STARTUP", f"pricing={cost_description}")

    # Payment lifecycle hooks for logging
    async def _before_request(method, params, request):
        token = request.headers.get("payment-signature", "")
        token_preview = f"{token[:16]}..." if len(token) > 16 else token or "(none)"
        log(_logger, "PAYMENT", "VERIFY", f"method={method} token={token_preview}")

    async def _after_request(method, response, request):
        status = getattr(response, "status_code", "ok")
        log(_logger, "PAYMENT", "VERIFIED", f"method={method} status={status}")

    async def _on_error(method, exc, request):
        log(_logger, "PAYMENT", "ERROR", f"method={method} error={exc}")

    hooks = {
        "beforeRequest": _before_request,
        "afterRequest": _after_request,
        "onError": _on_error,
    }

    # Create FastAPI app with health check
    fastapi_app = FastAPI(title="Creative Generation Selling Agent (AgentCore)")

    @fastapi_app.get("/ping")
    async def ping():
        return {"status": "ok"}

    # Pass our app to PaymentsA2AServer so it adds A2A + payment routes to it
    result = PaymentsA2AServer.start(
        agent_card=agent_card,
        executor=executor,
        payments_service=payments,
        port=PORT,
        hooks=hooks,
        app=fastapi_app,
    )

    # Add header remapping middleware AFTER PaymentsA2AServer.start() so it
    # wraps the payment middleware. Starlette executes middleware in reverse
    # order of addition, so this middleware runs FIRST — remapping the
    # AgentCore custom header to payment-signature before the payment
    # middleware checks for it.
    fastapi_app.add_middleware(AgentCoreHeaderMiddleware)
    log(_logger, "SERVER", "STARTUP", "AgentCore header remapping middleware active")

    # Use uvicorn.run() directly so we can bind to 0.0.0.0 (required in containers)
    import uvicorn

    uvicorn.run(fastapi_app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
