"""
FastAPI server wrapping the Strands agent for local development.

Payment protection handled by @requires_payment on tools.

Usage:
    poetry run agent
"""

import base64
import json
import os
import sys

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

PORT = int(os.getenv("PORT", "3030"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

if not OPENAI_API_KEY:
    print("OPENAI_API_KEY is required. Set it in .env file.")
    sys.exit(1)

model = OpenAIModel(
    client_args={"api_key": OPENAI_API_KEY},
    model_id=os.getenv("MODEL_ID", "gpt-4o-mini"),
)
agent = create_agent(model)

app = FastAPI(
    title="Agent Evaluator",
    description="Agent QA and health monitoring service with x402 payment-protected tools",
)


class DataRequest(BaseModel):
    query: str


@app.post("/data")
async def data(request: Request, body: DataRequest) -> JSONResponse:
    """Query the agent evaluator through the Strands agent."""
    try:
        payment_token = request.headers.get("payment-signature", "")
        state = {"payment_token": payment_token} if payment_token else {}

        result = agent(body.query, invocation_state=state)

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

        settlement = state.get("payment_settlement")
        credits = int(settlement.credits_redeemed) if settlement else 0
        analytics.record_request("request", credits)

        return JSONResponse(content={
            "response": str(result),
            "credits_used": credits,
        })

    except Exception as error:
        print(f"Error in /data: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )


@app.get("/pricing")
async def pricing() -> JSONResponse:
    """Get pricing information."""
    return JSONResponse(content={
        "planId": NVM_PLAN_ID,
        "tiers": PRICING_TIERS,
    })


@app.get("/stats")
async def stats() -> JSONResponse:
    """Get usage statistics."""
    return JSONResponse(content=analytics.get_stats())


@app.get("/health")
async def health() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok"})


def main():
    """Run the FastAPI server."""
    print(f"Agent Evaluator running on http://localhost:{PORT}")
    print(f"\nPlan ID: {NVM_PLAN_ID}")
    print("\nEndpoints:")
    print("  POST /data     - Query agent evaluator (x402 payment required)")
    print("  GET  /pricing  - View pricing tiers")
    print("  GET  /stats    - View usage analytics")
    print("  GET  /health   - Health check")

    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
