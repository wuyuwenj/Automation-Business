"""FastAPI server wrapping the creative Strands agent for local development."""

import base64
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from strands.models.openai import OpenAIModel

from payments_py.x402.strands import extract_payment_required

from .analytics import analytics
from .pricing import PRICING_TIERS
from .strands_agent import NVM_PLAN_ID, create_agent

PORT = int(os.getenv("PORT", "3000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

if not OPENAI_API_KEY:
    print("OPENAI_API_KEY is required for local Strands routing in agent.py.")
    sys.exit(1)

model = OpenAIModel(
    client_args={"api_key": OPENAI_API_KEY},
    model_id=os.getenv("MODEL_ID", "gpt-4o-mini"),
)
agent = create_agent(model)

app = FastAPI(
    title="Creative Generation Selling Agent",
    description="Strands AI agent with x402 payment-protected creative tools",
)


class CreativeRequest(BaseModel):
    query: str


def _maybe_parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return None


def _iter_structured_values(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_structured_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_structured_values(item)
    elif isinstance(node, str):
        parsed = _maybe_parse_json(node)
        if parsed is not None:
            yield from _iter_structured_values(parsed)


def _extract_landing_page_payload(messages: list) -> dict[str, Any] | None:
    candidate = None
    for msg in messages:
        for value in _iter_structured_values(msg):
            if not isinstance(value, dict):
                continue
            if "suggested_filename" in value and (
                "html" in value or "saved_path" in value or "preview_url" in value
            ):
                candidate = value
    if not candidate:
        return None

    payload = {
        "summary": candidate.get("summary", ""),
        "suggested_filename": candidate.get("suggested_filename", ""),
        "saved_path": candidate.get("saved_path", ""),
        "preview_url": candidate.get("preview_url", ""),
        "download_url": candidate.get("download_url", ""),
        "storage": candidate.get("storage", ""),
        "html": candidate.get("html", ""),
    }
    if payload["saved_path"]:
        payload["preview_command"] = f'open "{payload["saved_path"]}"'
    return payload


@app.post("/creative")
async def creative(request: Request, body: CreativeRequest) -> JSONResponse:
    """Generate creative assets through the Strands agent.

    Payment is handled by @requires_payment on each tool. If no valid
    token is provided, the tool returns a PaymentRequired error which
    we translate into an HTTP 402 response with the standard headers.
    """
    try:
        payment_token = request.headers.get("payment-signature", "")
        state = {"payment_token": payment_token} if payment_token else {}

        result = agent(body.query, invocation_state=state)

        # Check if payment was required but not fulfilled
        payment_required = extract_payment_required(agent.messages)
        if payment_required and not state.get("payment_settlement"):
            encoded = base64.b64encode(
                json.dumps(payment_required).encode()
            ).decode()
            return JSONResponse(
                status_code=402,
                content={
                    "error": "Payment Required",
                    "message": str(result),
                },
                headers={"payment-required": encoded},
            )

        # Success — record analytics
        settlement = state.get("payment_settlement")
        credits = int(settlement.credits_redeemed) if settlement else 0
        analytics.record_request("request", credits)

        response_body = {
            "response": str(result),
            "credits_used": credits,
        }
        landing_page = _extract_landing_page_payload(agent.messages)
        if landing_page:
            response_body["landing_page"] = landing_page

        return JSONResponse(content=response_body)

    except Exception as error:
        print(f"Error in /creative: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )


@app.get("/pricing")
async def pricing() -> JSONResponse:
    """Get pricing information (unprotected)."""
    return JSONResponse(content={
        "planId": NVM_PLAN_ID,
        "tiers": PRICING_TIERS,
    })


@app.get("/stats")
async def stats() -> JSONResponse:
    """Get usage statistics (unprotected)."""
    return JSONResponse(content=analytics.get_stats())


@app.get("/health")
async def health() -> JSONResponse:
    """Health check endpoint (unprotected)."""
    return JSONResponse(content={"status": "ok"})


def main():
    """Run the FastAPI server."""
    print(f"Creative Generation Selling Agent running on http://localhost:{PORT}")
    print("\nPayment protection via @requires_payment on Strands tools")
    print(f"Plan ID: {NVM_PLAN_ID}")
    print("\nEndpoints:")
    print("  POST /creative - Generate creative assets")
    print("  GET  /pricing  - View pricing tiers")
    print("  GET  /stats    - View usage analytics")
    print("  GET  /health   - Health check")

    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
