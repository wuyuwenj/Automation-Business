"""
Bedrock-powered buyer agent for Fargate deployment.

Web server with SSE streaming chat, using Amazon Bedrock for LLM inference
and Nevermined x402 for payments to seller agents.

Usage:
    PORT=8000 python buyer.py

Requires:
    NVM_API_KEY, NVM_PLAN_ID, NVM_AGENT_ID, SELLER_A2A_URL, AWS_REGION
"""

import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from strands import Agent
from strands.models.bedrock import BedrockModel

from payments_py import Payments, PaymentOptions

# --- Config ---
NVM_API_KEY = os.environ.get("NVM_API_KEY", "")
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID = os.environ.get("NVM_PLAN_ID", "")
NVM_AGENT_ID = os.environ.get("NVM_AGENT_ID", "")
SELLER_A2A_URL = os.environ.get("SELLER_A2A_URL", "")
PORT = int(os.getenv("PORT", "8000"))
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

if not NVM_API_KEY:
    print("NVM_API_KEY is required.")
    sys.exit(1)

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)

# --- Seller registry ---

_sellers: dict[str, dict] = {}


def _discover_seller(url: str) -> dict | None:
    """Fetch agent card from a seller URL and register it."""
    import httpx

    for path in ["/.well-known/agent.json", "/.well-known/agent-card.json"]:
        try:
            resp = httpx.get(f"{url.rstrip('/')}{path}", timeout=10.0)
            if resp.status_code == 200:
                card = resp.json()
                _sellers[url] = card
                return card
        except Exception:
            continue
    return None


def _get_payment_info(card: dict) -> dict:
    """Extract Nevermined payment info from agent card capabilities."""
    caps = card.get("capabilities", {})
    for ext in caps.get("extensions", []):
        if "nevermined" in ext.get("uri", "").lower() or "payment" in ext.get("uri", "").lower():
            return ext.get("params", {})
    return {}


# --- Tools ---

from strands import tool


@tool
def list_sellers() -> dict:
    """List all registered sellers and their capabilities."""
    if not _sellers:
        return {"status": "success", "content": [{"text": "No sellers registered. Use discover_agent to find one."}]}
    lines = []
    for url, card in _sellers.items():
        name = card.get("name", "Unknown")
        skills = [s.get("name", s.get("id", "")) for s in card.get("skills", [])]
        lines.append(f"- {name} ({url}): skills={', '.join(skills)}")
    return {"status": "success", "content": [{"text": "Registered sellers:\n" + "\n".join(lines)}]}


@tool
def discover_agent(url: str) -> dict:
    """Discover and register a seller agent by URL.

    Args:
        url: The seller agent's base URL.
    """
    card = _discover_seller(url)
    if card:
        payment = _get_payment_info(card)
        name = card.get("name", "Unknown")
        skills = [s.get("name", s.get("id", "")) for s in card.get("skills", [])]
        text = f"Discovered '{name}' at {url}\nSkills: {', '.join(skills)}"
        if payment:
            text += f"\nPayment: plan={str(payment.get('planId', ''))[:20]}... credits={payment.get('credits', '?')}"
        return {"status": "success", "content": [{"text": text}]}
    return {"status": "error", "content": [{"text": f"Could not discover agent at {url}"}]}


@tool
def check_balance() -> dict:
    """Check current credit balance for the payment plan."""
    try:
        balance = payments.get_plan_balance(NVM_PLAN_ID)
        return {"status": "success", "content": [{"text": f"Balance: {balance}"}]}
    except Exception as e:
        return {"status": "error", "content": [{"text": f"Balance check failed: {e}"}]}


@tool
def purchase_a2a(seller_url: str, query: str) -> dict:
    """Send a paid A2A request to a seller agent.

    Args:
        seller_url: The seller's base URL.
        query: The query/task to send to the seller.
    """
    from uuid import uuid4

    from a2a.types import Message, MessageSendParams, TextPart as A2ATextPart
    from payments_py.a2a.payments_client import PaymentsClient

    card = _sellers.get(seller_url)
    if not card:
        card = _discover_seller(seller_url)
    if not card:
        return {"status": "error", "content": [{"text": f"Seller not found at {seller_url}"}]}

    payment_info = _get_payment_info(card)
    plan_id = payment_info.get("planId", NVM_PLAN_ID)
    agent_id = payment_info.get("agentId", NVM_AGENT_ID)

    try:
        client = PaymentsClient(
            agent_base_url=seller_url,
            payments=payments,
            agent_id=agent_id,
            plan_id=plan_id,
        )

        params = MessageSendParams(
            message=Message(
                message_id=str(uuid4()),
                role="user",
                parts=[A2ATextPart(text=query)],
            )
        )

        print(f"[PURCHASE] Sending A2A request to {seller_url}")

        # Collect streaming events
        async def _collect():
            events = []
            async for event in client.send_message_stream(params):
                events.append(event)
            return events

        events = asyncio.run(_collect())

        # Extract response from events
        for event in reversed(events):
            if isinstance(event, tuple):
                task = event[0]
            else:
                task = event

            status = getattr(task, "status", None)
            if not status:
                continue

            state = status.state
            state_val = state.value if hasattr(state, "value") else str(state)

            if state_val == "completed":
                message = getattr(status, "message", None)
                parts = getattr(message, "parts", []) if message else []
                text = ""
                for part in parts:
                    if hasattr(part, "root"):
                        part = part.root
                    if hasattr(part, "text"):
                        text += part.text
                credits_used = 0
                metadata = getattr(task, "metadata", None) or {}
                if isinstance(metadata, dict):
                    credits_used = metadata.get("creditsUsed", 0)
                return {"status": "success", "content": [{"text": text or "Task completed (no text)."}], "credits_used": credits_used}

            if state_val == "failed":
                message = getattr(status, "message", None)
                parts = getattr(message, "parts", []) if message else []
                text = ""
                for part in parts:
                    if hasattr(part, "root"):
                        part = part.root
                    if hasattr(part, "text"):
                        text += part.text
                return {"status": "error", "content": [{"text": text or "Task failed."}]}

        return {"status": "success", "content": [{"text": "Task completed but returned no events."}]}

    except Exception as e:
        print(f"[PURCHASE] Failed: {e}")
        return {"status": "error", "content": [{"text": f"Purchase failed: {e}"}]}


# --- Agent ---

model = BedrockModel(model_id=BEDROCK_MODEL_ID)
agent = Agent(
    model=model,
    tools=[list_sellers, discover_agent, check_balance, purchase_a2a],
    system_prompt=(
        "You are a data buying agent. You help users find and purchase data from seller agents.\n"
        "1. Use discover_agent to find sellers by URL\n"
        "2. Use list_sellers to see registered sellers\n"
        "3. Use purchase_a2a to buy data from sellers\n"
        "4. Use check_balance to monitor credits\n"
        f"Default seller URL: {SELLER_A2A_URL}"
    ),
)
agent_lock = asyncio.Lock()


# --- Web server ---

app = FastAPI(title="Buyer Agent (Bedrock)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/chat")
async def chat(request: Request):
    """Stream a chat response via SSE."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    message = (body.get("message", "") or body.get("prompt", "")).strip()
    if not message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    async def event_generator():
        full_response = ""
        try:
            async with agent_lock:
                async for event in agent.stream_async(message):
                    if "data" in event:
                        chunk = event["data"]
                        full_response += chunk
                        yield {"event": "token", "data": json.dumps({"text": chunk})}
                    elif "current_tool_use" in event:
                        tool_name = event["current_tool_use"].get("name", "unknown")
                        yield {"event": "tool_use", "data": json.dumps({"name": tool_name})}
            yield {"event": "done", "data": json.dumps({"text": full_response})}
        except Exception as exc:
            yield {"event": "error", "data": json.dumps({"error": str(exc)})}

    return EventSourceResponse(event_generator())


@app.get("/api/sellers")
async def get_sellers():
    """Return all registered sellers."""
    return JSONResponse([
        {"url": url, "name": card.get("name", ""), "skills": card.get("skills", [])}
        for url, card in _sellers.items()
    ])


@app.get("/ping")
async def ping():
    return {"status": "ok"}


# --- Auto-discover seller on startup ---

if SELLER_A2A_URL:
    _discover_seller(SELLER_A2A_URL)


def main():
    import uvicorn

    print(f"Buyer Agent (Bedrock) starting on port {PORT}")
    print(f"  model={BEDROCK_MODEL_ID} env={NVM_ENVIRONMENT}")
    if SELLER_A2A_URL:
        print(f"  seller={SELLER_A2A_URL}")
        if SELLER_A2A_URL in _sellers:
            print(f"  seller discovered: {_sellers[SELLER_A2A_URL].get('name', 'unknown')}")
        else:
            print(f"  seller not reachable yet (will retry on first request)")

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
