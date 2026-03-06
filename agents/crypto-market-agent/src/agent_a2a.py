"""
A2A server entry point for the crypto market intelligence agent.

Runs as an A2A-compliant agent with payment validation middleware.

Usage:
    poetry run agent-a2a
    poetry run agent-a2a --tools price --port 9011
    poetry run agent-a2a --tools analysis,report --port 9012
"""

import argparse
import asyncio
import datetime
import os
import sys
import threading
import time
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from strands import Agent
from strands.models.openai import OpenAIModel

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    AgentSkill,
    Message,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from payments_py import Payments, PaymentOptions
from payments_py.a2a.agent_card import build_payment_agent_card
from payments_py.a2a.server import PaymentsA2AServer

from payments_py.a2a.payments_request_handler import PaymentsRequestHandler

from .log import get_logger, log
from .observability import create_observability_model
from .strands_agent_plain import ALL_TOOLS, create_plain_agent, resolve_tools

load_dotenv()

NVM_API_KEY = os.environ["NVM_API_KEY"]
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID = os.environ["NVM_PLAN_ID"]
NVM_AGENT_ID = os.getenv("NVM_AGENT_ID", "")
OBSERVABILITY_ENABLED = os.getenv("OBSERVABILITY_ENABLED", "false").lower() == "true"

_logger = get_logger("crypto")

if not NVM_AGENT_ID:
    log(_logger, "SERVER", "ERROR", "NVM_AGENT_ID is required for A2A mode. "
        "Set it in your .env file (find it in the Nevermined App agent settings).")
    sys.exit(1)

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)


# ---------------------------------------------------------------------------
# Custom executor
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _make_status_event(
    task_id: str,
    context_id: str,
    state: TaskState,
    text: str,
    credits_used: int | None = None,
    agent_request_id: str | None = None,
    final: bool = True,
) -> TaskStatusUpdateEvent:
    metadata = {}
    if credits_used is not None:
        metadata["creditsUsed"] = credits_used
    if agent_request_id:
        metadata["agentRequestId"] = agent_request_id
    metadata = metadata or None
    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        status=TaskStatus(
            state=state,
            message=Message(
                message_id=str(uuid4()),
                role=Role.agent,
                parts=[{"kind": "text", "text": text}],
                task_id=task_id,
                context_id=context_id,
            ),
            timestamp=_now_iso(),
        ),
        metadata=metadata,
        final=final,
    )


def _extract_text_from_parts(parts) -> str:
    fragments = []
    for part in parts:
        if hasattr(part, "root"):
            part = part.root
        if hasattr(part, "text"):
            fragments.append(part.text)
        elif isinstance(part, dict) and part.get("kind") == "text":
            fragments.append(part.get("text", ""))
    return "".join(fragments)


class StrandsA2AExecutor(AgentExecutor):
    """Execute A2A requests by delegating to a Strands agent."""

    def __init__(
        self,
        agent: Agent,
        credit_map: dict[str, int] | None = None,
        payments_service: Payments | None = None,
        tool_names: list[str] | None = None,
    ):
        self._agent = agent
        self._credit_map = credit_map or {}
        self._payments = payments_service
        self._tool_names = tool_names
        self._log = get_logger("crypto.executor")
        self.handler: PaymentsRequestHandler | None = None

    async def execute(self, context, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid4())
        context_id = context.context_id or str(uuid4())

        if not getattr(context, "current_task", None):
            await event_queue.enqueue_event(
                Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(
                        state=TaskState.submitted, timestamp=_now_iso()
                    ),
                    history=[],
                )
            )

        await event_queue.enqueue_event(
            _make_status_event(
                task_id, context_id, TaskState.working,
                "Processing request...", final=False,
            )
        )

        user_text = self._extract_user_text(context) or "Hello"
        log(self._log, "EXECUTOR", "RECEIVED",
            f'query="{user_text[:80]}" task={task_id[:8]}')

        agent_request = getattr(self.handler, "latest_agent_request", None) if self.handler else None
        agent_request_id = getattr(self.handler, "latest_agent_request_id", None) if self.handler else None
        agent = self._agent
        invocation_state = {}

        if OBSERVABILITY_ENABLED and agent_request and self._payments:
            api_key = os.environ.get("OPENAI_API_KEY", "")
            model_id = os.environ.get("MODEL_ID", "gpt-4o-mini")
            obs_model = create_observability_model(
                self._payments, agent_request, api_key, model_id,
            )
            if obs_model:
                agent = create_plain_agent(obs_model, self._tool_names)
                log(self._log, "OBSERVABILITY", "ENABLED",
                    f"request_id={agent_request_id}")

        msg_offset = len(agent.messages)
        try:
            result = await asyncio.to_thread(
                agent, user_text, invocation_state=invocation_state,
            )
            response_text = str(result)
        except Exception as exc:
            log(self._log, "EXECUTOR", "FAILED", str(exc))
            await event_queue.enqueue_event(
                _make_status_event(
                    task_id, context_id, TaskState.failed,
                    f"Error: {exc}", credits_used=0,
                    agent_request_id=agent_request_id,
                )
            )
            return

        credits_used = self._calculate_credits(agent.messages[msg_offset:])
        log(self._log, "EXECUTOR", "COMPLETED",
            f"credits_used={credits_used} response={len(response_text)} chars")

        await event_queue.enqueue_event(
            _make_status_event(
                task_id, context_id, TaskState.completed,
                response_text, credits_used=credits_used,
                agent_request_id=agent_request_id,
            )
        )

    async def cancel(self, context, event_queue: EventQueue) -> None:
        task_id = getattr(context, "task_id", None) or str(uuid4())
        context_id = getattr(context, "context_id", None) or str(uuid4())
        await event_queue.enqueue_event(
            _make_status_event(
                task_id, context_id, TaskState.canceled,
                "Task cancelled.", credits_used=0,
            )
        )

    @staticmethod
    def _extract_user_text(context) -> str:
        message = getattr(context, "message", None)
        if not message:
            return ""
        parts = getattr(message, "parts", [])
        return _extract_text_from_parts(parts)

    def _calculate_credits(self, messages: list) -> int:
        total = 0
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    credits = self._credit_map.get(name, 1)
                    log(self._log, "EXECUTOR", "TOOL_USE",
                        f"{name} ({credits} cr)")
                    total += credits
        return total or 1


# ---------------------------------------------------------------------------
# Self-registration with buyer
# ---------------------------------------------------------------------------

def _register_with_buyer(buyer_url: str, agent_url: str):
    time.sleep(2)
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "messageId": str(uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": agent_url}],
            }
        },
    }
    _reg_log = get_logger("crypto.register")
    log(_reg_log, "REGISTER", "SENDING", f"buyer={buyer_url} self={agent_url}")

    for attempt in range(1, 4):
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(buyer_url, json=payload)
            if resp.status_code == 200:
                log(_reg_log, "REGISTER", "SUCCESS", f"registered with {buyer_url}")
                return
            log(_reg_log, "REGISTER", "FAILED", f"attempt {attempt}: HTTP {resp.status_code}")
        except Exception as exc:
            log(_reg_log, "REGISTER", "FAILED", f"attempt {attempt}: {exc}")
        time.sleep(2)

    log(_reg_log, "REGISTER", "ERROR", "could not register with buyer after 3 attempts")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args():
    parser = argparse.ArgumentParser(description="Crypto Market Agent -- A2A Mode")
    parser.add_argument(
        "--tools",
        nargs="*",
        choices=list(ALL_TOOLS.keys()),
        default=None,
        help="Tools to expose (default: all). Options: price, analysis, report",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("A2A_PORT", os.getenv("PORT", "9010"))),
        help="Port to listen on (default: A2A_PORT or PORT env, or 9010)",
    )
    parser.add_argument(
        "--buyer-url",
        default=os.getenv("BUYER_URL", ""),
        help="Buyer registration URL for auto-registration",
    )
    return parser.parse_args()


def main():
    """Start the A2A server."""
    args = _parse_args()
    port = args.port
    tool_names = args.tools
    buyer_url = args.buyer_url

    tools_list, credit_map, skills = resolve_tools(tool_names)

    cost_parts = [f"{name}={cost}" for name, cost in credit_map.items()]
    cost_description = "Credits vary by tool: " + ", ".join(cost_parts)

    base_agent_card = {
        "name": "Crypto Market Intelligence Agent",
        "description": (
            "AI-powered crypto and DeFi market intelligence agent. "
            "Provides real-time prices, OHLCV trend analysis, and DeFi protocol reports."
        ),
        "url": os.getenv("AGENT_URL", f"http://localhost:{port}"),
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

    model = OpenAIModel(
        client_args={"api_key": os.environ.get("OPENAI_API_KEY", "")},
        model_id=os.getenv("MODEL_ID", "gpt-4o-mini"),
    )

    agent = create_plain_agent(model, tool_names)
    executor = StrandsA2AExecutor(
        agent, credit_map,
        payments_service=payments,
        tool_names=tool_names,
    )

    tool_label = ", ".join(tool_names) if tool_names else "all"
    log(_logger, "SERVER", "STARTUP",
        f"Crypto Market Agent -- A2A Mode on port {port}")
    log(_logger, "SERVER", "STARTUP",
        f"card=http://localhost:{port}/.well-known/agent.json")
    log(_logger, "SERVER", "STARTUP",
        f"plan={NVM_PLAN_ID} agent={NVM_AGENT_ID} env={NVM_ENVIRONMENT}")
    log(_logger, "SERVER", "STARTUP",
        f"tools=[{tool_label}] pricing={cost_description}")
    obs_label = "enabled" if OBSERVABILITY_ENABLED else "disabled"
    log(_logger, "SERVER", "STARTUP", f"observability={obs_label}")

    if buyer_url:
        agent_url = f"http://localhost:{port}"
        log(_logger, "SERVER", "STARTUP", f"will register with buyer at {buyer_url}")
        thread = threading.Thread(
            target=_register_with_buyer,
            args=(buyer_url, agent_url),
            daemon=True,
        )
        thread.start()

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

    from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore

    handler = PaymentsRequestHandler(
        agent_card=agent_card,
        task_store=InMemoryTaskStore(),
        agent_executor=executor,
        payments_service=payments,
    )
    executor.handler = handler

    result = PaymentsA2AServer.start(
        agent_card=agent_card,
        executor=executor,
        payments_service=payments,
        port=port,
        hooks=hooks,
        custom_request_handler=handler,
    )

    # Override uvicorn host to bind to all interfaces (for EC2/remote hosting)
    result.server.config.host = "0.0.0.0"
    asyncio.run(result.server.serve())


if __name__ == "__main__":
    main()
