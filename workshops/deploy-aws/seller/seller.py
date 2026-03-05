"""
Bedrock-powered seller agent for Fargate deployment.

A2A server with Nevermined x402 payment protection, using Amazon Bedrock
for all LLM inference (no OpenAI key needed).

Usage:
    PORT=9000 python seller.py

Requires:
    NVM_API_KEY, NVM_PLAN_ID, NVM_AGENT_ID, AWS_REGION
"""

import json
import os
import re
import sys

import boto3
import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

from payments_py import Payments, PaymentOptions
from payments_py.a2a.agent_card import build_payment_agent_card
from payments_py.a2a.server import PaymentsA2AServer

load_dotenv()

# --- Config ---
NVM_API_KEY = os.environ["NVM_API_KEY"]
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID = os.environ["NVM_PLAN_ID"]
NVM_AGENT_ID = os.getenv("NVM_AGENT_ID", "")
PORT = int(os.getenv("PORT", "9000"))
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

if not NVM_AGENT_ID:
    print("NVM_AGENT_ID is required. Set it in .env or env vars.")
    sys.exit(1)

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)

# --- Bedrock client for tool-level LLM calls ---
bedrock_runtime = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def _bedrock_chat(system_prompt: str, user_content: str, max_tokens: int = 500) -> str:
    """Call Bedrock converse API for tool-level LLM inference."""
    response = bedrock_runtime.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": user_content}]}],
        system=[{"text": system_prompt}],
        inferenceConfig={"maxTokens": max_tokens},
    )
    return response["output"]["message"]["content"][0]["text"]


# --- Tools ---

@tool
def search_data(query: str) -> dict:
    """Search the web for data. Costs 1 credit per request.

    Args:
        query: Search query string.
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1"},
            )
            data = resp.json()

        results = []
        for topic in data.get("RelatedTopics", [])[:5]:
            if isinstance(topic, dict) and "Text" in topic:
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", ""),
                })

        abstract = data.get("Abstract", "")
        if abstract:
            results.insert(0, {
                "title": data.get("Heading", "Overview"),
                "url": data.get("AbstractURL", ""),
                "snippet": abstract,
            })

        if results:
            details = "\n".join(f"- {r['title']}: {r['snippet'][:200]}" for r in results)
            text = f"Found {len(results)} results for '{query}'\n\n{details}"
        else:
            text = f"No results found for '{query}'."

        return {"status": "success", "content": [{"text": text}], "results": results}
    except Exception as e:
        return {"status": "error", "content": [{"text": f"Search failed: {e}"}], "results": []}


@tool
def summarize_data(content: str, focus: str = "key_findings") -> dict:
    """Summarize content with LLM-powered analysis. Costs 5 credits.

    Args:
        content: The text content to summarize.
        focus: Focus area - 'key_findings', 'action_items', 'trends', or 'risks'.
    """
    focus_prompts = {
        "key_findings": "Extract the most important findings and insights.",
        "action_items": "Identify actionable recommendations and next steps.",
        "trends": "Identify emerging trends and patterns.",
        "risks": "Identify potential risks and concerns.",
    }
    focus_instruction = focus_prompts.get(focus, focus_prompts["key_findings"])

    try:
        system = (
            "You are a data analyst. Summarize the provided content. "
            f"{focus_instruction}\n\n"
            "Return your response in this exact format:\n"
            "SUMMARY: <2-3 sentence summary>\n"
            "KEY POINTS:\n- <point 1>\n- <point 2>\n- <point 3>"
        )
        response_text = _bedrock_chat(system, content[:4000])

        summary = response_text
        key_points = []
        if "KEY POINTS:" in response_text:
            parts = response_text.split("KEY POINTS:")
            summary = parts[0].replace("SUMMARY:", "").strip()
            key_points = [
                p.strip().lstrip("- ")
                for p in parts[1].strip().split("\n")
                if p.strip().startswith("-")
            ]

        return {
            "status": "success",
            "content": [{"text": response_text}],
            "summary": summary,
            "key_points": key_points,
        }
    except Exception as e:
        return {"status": "error", "content": [{"text": f"Summarization failed: {e}"}]}


@tool
def research_data(query: str, depth: str = "standard") -> dict:
    """Conduct market research combining search, content fetching, and synthesis. Costs 10 credits.

    Args:
        query: The research topic or question.
        depth: Research depth - 'standard' or 'deep'.
    """
    try:
        # Step 1: Web search
        search_result = search_data(query=query)
        raw_results = search_result.get("results", [])

        if not raw_results:
            return {
                "status": "success",
                "content": [{"text": f"No data found for: '{query}'"}],
                "report": f"No data found for: {query}",
                "sources": [],
            }

        sources = [{"title": r.get("title", ""), "url": r["url"]} for r in raw_results if r.get("url")]
        content_pieces = [r.get("snippet", "") for r in raw_results if r.get("snippet")]

        if depth == "deep":
            for r in raw_results[:3]:
                url = r.get("url", "")
                if url:
                    try:
                        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                            resp = client.get(url, headers={"User-Agent": "DataSellingAgent/1.0"})
                            text = re.sub(r"<[^>]+>", " ", resp.text)
                            text = re.sub(r"\s+", " ", text).strip()[:2000]
                            if text:
                                content_pieces.append(text)
                    except Exception:
                        pass

        combined = "\n\n".join(content_pieces)

        system = (
            "You are a market research analyst. Write a concise research report:\n"
            "1. Executive Summary (2-3 sentences)\n"
            "2. Key Findings (3-5 bullet points)\n"
            "3. Market Trends (if applicable)\n"
            "4. Recommendations (2-3 actionable items)"
        )
        report = _bedrock_chat(system, f"Research query: {query}\n\nData:\n{combined[:6000]}", max_tokens=1000)

        return {"status": "success", "content": [{"text": report}], "report": report, "sources": sources}
    except Exception as e:
        return {"status": "error", "content": [{"text": f"Research failed: {e}"}], "report": "", "sources": []}


# --- A2A Executor ---

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Part,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

CREDIT_MAP = {"search_data": 1, "summarize_data": 5, "research_data": 10}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_status_event(task_id, context_id, state, text, credits_used=None):
    parts: list[Part] = [Part(root=TextPart(text=text))]
    metadata = {}
    if credits_used is not None:
        metadata["creditsUsed"] = str(credits_used)
    return TaskStatusUpdateEvent(
        taskId=task_id,
        contextId=context_id,
        status=TaskStatus(
            state=state,
            timestamp=_now_iso(),
            message={"role": "agent", "parts": parts, "messageId": str(uuid4())},
        ),
        metadata=metadata if metadata else None,
        final=state in (TaskState.completed, TaskState.failed, TaskState.canceled),
    )


class SellerExecutor(AgentExecutor):
    """Execute buyer requests using the Strands agent."""

    def __init__(self, agent: Agent):
        self._agent = agent

    async def execute(self, context, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid4())
        context_id = context.context_id or str(uuid4())

        # Publish initial task
        if not getattr(context, "current_task", None):
            await event_queue.enqueue_event(
                Task(
                    id=task_id, context_id=context_id,
                    status=TaskStatus(state=TaskState.submitted, timestamp=_now_iso()),
                    history=[],
                )
            )

        await event_queue.enqueue_event(
            _make_status_event(task_id, context_id, TaskState.working, "Processing...")
        )

        # Extract user text
        user_text = ""
        message = getattr(context, "message", None)
        if message:
            for part in getattr(message, "parts", []):
                if hasattr(part, "root") and hasattr(part.root, "text"):
                    user_text += part.root.text
                elif hasattr(part, "text"):
                    user_text += part.text
        user_text = user_text.strip() or "Hello"

        print(f"[EXECUTOR] query=\"{user_text[:80]}\" task={task_id[:8]}")

        # Run the Strands agent
        msg_offset = len(self._agent.messages)
        try:
            result = await asyncio.to_thread(self._agent, user_text)
            response_text = str(result)
        except Exception as exc:
            print(f"[EXECUTOR] FAILED: {exc}")
            await event_queue.enqueue_event(
                _make_status_event(task_id, context_id, TaskState.failed, f"Error: {exc}", credits_used=0)
            )
            return

        # Calculate credits from tool usage
        credits_used = 0
        for msg in self._agent.messages[msg_offset:]:
            if not isinstance(msg, dict):
                continue
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    credits_used += CREDIT_MAP.get(name, 1)

        print(f"[EXECUTOR] COMPLETED credits_used={credits_used}")

        await event_queue.enqueue_event(
            _make_status_event(task_id, context_id, TaskState.completed, response_text, credits_used=credits_used)
        )

    async def cancel(self, context, event_queue: EventQueue) -> None:
        task_id = getattr(context, "task_id", None) or str(uuid4())
        context_id = getattr(context, "context_id", None) or str(uuid4())
        await event_queue.enqueue_event(
            _make_status_event(task_id, context_id, TaskState.canceled, "Cancelled", credits_used=0)
        )


# --- Main ---

def main():
    tools = [search_data, summarize_data, research_data]
    credit_map = CREDIT_MAP
    cost_parts = [f"{name}={cost}" for name, cost in credit_map.items()]
    cost_description = "Credits vary by tool: " + ", ".join(cost_parts)

    skills = [
        {"id": "search_data", "name": "Web Search", "description": "Search the web for data. Costs 1 credit."},
        {"id": "summarize_data", "name": "Content Summarization", "description": "Summarize content with LLM analysis. Costs 5 credits."},
        {"id": "research_data", "name": "Market Research", "description": "Multi-step research pipeline. Costs 10 credits."},
    ]

    agent_card = build_payment_agent_card(
        {
            "name": "Data Selling Agent (Bedrock)",
            "description": "AI-powered data agent using Amazon Bedrock for inference.",
            "url": f"http://localhost:{PORT}",
            "version": "0.1.0",
            "skills": skills,
            "capabilities": {"streaming": True, "pushNotifications": False},
        },
        {
            "paymentType": "dynamic",
            "credits": min(credit_map.values()),
            "planId": NVM_PLAN_ID,
            "agentId": NVM_AGENT_ID,
            "costDescription": cost_description,
        },
    )

    model = BedrockModel(model_id=BEDROCK_MODEL_ID)
    agent = Agent(model=model, tools=tools)
    executor = SellerExecutor(agent)

    fastapi_app = FastAPI(title="Data Selling Agent (Bedrock)")

    @fastapi_app.get("/ping")
    async def ping():
        return {"status": "ok"}

    print(f"Seller Agent (Bedrock) starting on port {PORT}")
    print(f"  model={BEDROCK_MODEL_ID} plan={NVM_PLAN_ID[:20]}... env={NVM_ENVIRONMENT}")

    PaymentsA2AServer.start(
        agent_card=agent_card,
        executor=executor,
        payments_service=payments,
        port=PORT,
        app=fastapi_app,
    )

    uvicorn.run(fastapi_app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
