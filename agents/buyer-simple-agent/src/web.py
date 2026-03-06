"""
Web server for the buyer agent with chat UI support.

Provides a FastAPI server with:
- POST /api/chat — SSE streaming chat with the Strands agent
- GET /api/sellers — List registered sellers
- GET /api/balance — Check credit balance and budget
- GET /api/logs/stream — SSE log stream
- A2A registration routes (same as registration_server.py)
- Static file serving for the React frontend

Usage:
    poetry run web
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import httpx

load_dotenv()

# Load secrets from AWS Secrets Manager if available (for ECS/App Runner)
try:
    import boto3
    _secret_name = os.getenv("AWS_SECRET_NAME", "hackathon/buyer-agent")
    _region = os.getenv("AWS_REGION", "us-west-2")
    _client = boto3.client("secretsmanager", region_name=_region)
    _resp = _client.get_secret_value(SecretId=_secret_name)
    for k, v in __import__("json").loads(_resp["SecretString"]).items():
        if k not in os.environ:
            os.environ[k] = v
    print(f"Loaded secrets from Secrets Manager ({_secret_name})")
except Exception:
    pass  # Local dev — .env is sufficient

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from starlette.responses import FileResponse

from .log import enable_web_logging, get_logger, log
from .openai_compat import create_openai_model, validate_openai_config
from .registration_server import RegistrationExecutor, _build_buyer_agent_card
from .strands_agent import (
    NVM_PLAN_ID,
    budget,
    comparison_memory,
    create_agent,
    ledger,
    payments,
    seller_registry,
)
from .tools.balance import check_balance_impl
from .zeroclick_mcp import ZeroClickMCPClient
from .zeroclick import build_offer_query, build_session_user_id, infer_signals

BUYER_PORT = int(os.getenv("BUYER_PORT", "8000"))

config_error = validate_openai_config()
if config_error:
    print(config_error)
    sys.exit(1)

# Create a shared model, but instantiate a fresh agent per request.
# Strands agents can enter an unrecoverable state after max_tokens errors.
model = create_openai_model()


def _create_web_agent():
    return create_agent(model, mode=os.getenv("BUYER_AGENT_MODE", "smart"))

# Serialize concurrent chat requests (Strands Agent is not thread-safe)
agent_lock = asyncio.Lock()

# Log broadcast: each SSE subscriber gets its own queue
log_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
_log_subscribers: set[asyncio.Queue] = set()
_log_history: list[dict] = []  # recent logs for new subscribers
_LOG_HISTORY_MAX = 200


async def _log_dispatcher():
    """Read from the single log_queue and fan out to all subscribers."""
    while True:
        entry = await log_queue.get()
        _log_history.append(entry)
        if len(_log_history) > _LOG_HISTORY_MAX:
            _log_history.pop(0)
        dead = []
        for q in _log_subscribers:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _log_subscribers.discard(q)


_logger = get_logger("buyer.web")

app = FastAPI(title="Buyer Agent Web")
_zeroclick_mcp = ZeroClickMCPClient()


def _zeroclick_signals_enabled() -> bool:
    raw = os.getenv("ZEROCLICK_SIGNALS_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _zeroclick_mcp_enabled() -> bool:
    raw = os.getenv("ZEROCLICK_MCP_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _zeroclick_llm_model() -> str:
    explicit = os.getenv("ZEROCLICK_LLM_MODEL", "").strip()
    if explicit:
        return explicit[:64]
    model_id = os.getenv("MODEL_ID", "gpt-4o").strip() or "gpt-4o"
    return f"openai/{model_id}"[:64]


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"


async def _broadcast_zeroclick_signals(request: Request, query: str) -> None:
    api_key = os.getenv("ZEROCLICK_API_KEY", "").strip()
    if not api_key or not _zeroclick_signals_enabled():
        return

    signals = infer_signals(query)
    if not signals:
        return

    ip_address = _client_ip(request)
    session_id = request.headers.get("x-zc-session-id", "").strip()
    user_id = build_session_user_id(session_id, ip_address)
    user_agent = request.headers.get("user-agent", "")
    user_locale = request.headers.get("accept-language", "en-US").split(",")[0].strip() or "en-US"
    body = {
        "userId": user_id,
        "ipAddress": ip_address,
        "signals": signals,
    }

    try:
        if _zeroclick_mcp_enabled():
            _zeroclick_mcp.configure_session(
                user_id,
                api_key,
                llm_model=_zeroclick_llm_model(),
                user_id=user_id,
                user_session_id=session_id or user_id,
                user_locale=user_locale,
                grouping_id="buyer-web-chat",
                user_ip=ip_address,
                user_agent=user_agent,
            )
            await _zeroclick_mcp.broadcast_signal(user_id, api_key, signals)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://zeroclick.dev/api/v2/signals",
                    headers={
                        "Content-Type": "application/json",
                        "x-zc-api-key": api_key,
                    },
                    json=body,
                )
                resp.raise_for_status()
        primary = signals[0]
        log(
            _logger,
            "ZEROCLICK",
            "MCP_SIGNAL" if _zeroclick_mcp_enabled() else "SIGNAL",
            f"category={primary.get('category')} subject={primary.get('subject', '')[:60]}",
        )
    except Exception as exc:
        log(_logger, "ZEROCLICK", "ERROR", f"signal broadcast failed: {exc}")


@app.on_event("startup")
async def _start_log_dispatcher():
    asyncio.create_task(_log_dispatcher())


# CORS for frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enable web log streaming
enable_web_logging(log_queue)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.post("/api/chat")
async def chat(request: Request):
    """Stream a chat response from the agent via SSE."""
    try:
        body = await request.json()
    except Exception as exc:
        log(_logger, "WEB", "ERROR", f"Failed to parse JSON body: {exc}")
        raw = (await request.body()).decode("utf-8", errors="replace")
        log(_logger, "WEB", "ERROR", f"Raw body: {raw[:200]}")
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    log(_logger, "WEB", "DEBUG", f"body keys={list(body.keys())}")
    message = (body.get("message", "") or body.get("prompt", "")).strip()
    if not message:
        log(_logger, "WEB", "ERROR", f"Empty message. Full body: {str(body)[:200]}")
        return JSONResponse({"error": "Empty message"}, status_code=400)

    log(_logger, "WEB", "RECEIVED", f'chat message: "{message[:80]}"')

    async def event_generator():
        full_response = ""
        try:
            async with agent_lock:
                agent = _create_web_agent()
                async for event in agent.stream_async(message):
                    if "data" in event:
                        chunk = event["data"]
                        full_response += chunk
                        yield {
                            "event": "token",
                            "data": json.dumps({"text": chunk}),
                        }
                    elif "current_tool_use" in event:
                        tool_info = event["current_tool_use"]
                        tool_name = tool_info.get("name", "unknown")
                        yield {
                            "event": "tool_use",
                            "data": json.dumps({"name": tool_name}),
                        }
            yield {
                "event": "done",
                "data": json.dumps({"text": full_response}),
            }
        except Exception as exc:
            err_text = str(exc)
            if "max_tokens" in err_text.lower():
                err_text = (
                    "This request exceeded the model token budget. "
                    "Try a narrower query or a seller that returns a shorter result."
                )
            log(_logger, "WEB", "ERROR", f"chat stream error: {exc}")
            yield {
                "event": "error",
                "data": json.dumps({"error": err_text}),
            }

    return EventSourceResponse(event_generator())


@app.get("/api/sellers")
async def get_sellers():
    """Return all registered sellers."""
    return JSONResponse(content=seller_registry.list_all(verbose=True))


@app.get("/api/balance")
async def get_balance():
    """Check credit balance and budget status."""
    balance_result = check_balance_impl(payments, NVM_PLAN_ID)
    budget_status = budget.get_status()
    return JSONResponse(content={
        "balance": balance_result,
        "budget": budget_status,
    })


@app.get("/api/config")
async def get_config():
    """Return non-secret frontend configuration loaded from env/Secrets Manager."""
    return JSONResponse(content={
        "zeroclickEnabled": bool(os.getenv("ZEROCLICK_API_KEY")),
        "zeroclickQuery": os.getenv("ZEROCLICK_QUERY", "AI tools for business"),
        "zeroclickDynamicAds": True,
        "zeroclickSignalCollectionEnabled": _zeroclick_signals_enabled(),
        "zeroclickMcpEnabled": _zeroclick_mcp_enabled(),
    })


@app.get("/api/zeroclick/offers")
async def get_zeroclick_offers(request: Request):
    """Fetch ZeroClick offers server-side using the configured API key."""
    api_key = os.getenv("ZEROCLICK_API_KEY", "").strip()
    if not api_key:
        return JSONResponse(
            content={"error": "ZeroClick API key not configured", "offers": []},
            status_code=503,
        )

    raw_query = (request.query_params.get("query") or "").strip()
    fallback_query = os.getenv("ZEROCLICK_QUERY", "AI tools for business")
    query = build_offer_query(raw_query, fallback_query)

    if raw_query and _zeroclick_signals_enabled():
        asyncio.create_task(_broadcast_zeroclick_signals(request, query))

    payload = {
        "method": "server",
        "ipAddress": _client_ip(request),
        "userAgent": request.headers.get("user-agent", ""),
        "query": query,
        "limit": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://zeroclick.dev/api/v2/offers",
                headers={
                    "Content-Type": "application/json",
                    "x-zc-api-key": api_key,
                },
                json=payload,
            )
            resp.raise_for_status()
            offers = resp.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        log(_logger, "WEB", "ERROR", f"zeroclick offers http={exc.response.status_code} detail={detail}")
        return JSONResponse(
            content={"error": f"ZeroClick offers failed: HTTP {exc.response.status_code}", "offers": []},
            status_code=502,
        )
    except Exception as exc:
        log(_logger, "WEB", "ERROR", f"zeroclick offers request failed: {exc}")
        return JSONResponse(
            content={"error": f"ZeroClick offers request failed: {exc}", "offers": []},
            status_code=502,
        )

    return JSONResponse(content={"offers": offers, "appliedQuery": query})


@app.get("/api/zeroclick/mcp/status")
async def get_zeroclick_mcp_status(request: Request):
    """Return ZeroClick MCP session diagnostics for the current web session."""
    api_key = os.getenv("ZEROCLICK_API_KEY", "").strip()
    if not api_key:
        return JSONResponse(
            content={"connected": False, "error": "ZEROCLICK_API_KEY not configured"},
            status_code=503,
        )

    session_id = request.headers.get("x-zc-session-id", "").strip()
    session_key = build_session_user_id(session_id, _client_ip(request))

    try:
        if _zeroclick_mcp_enabled():
            _zeroclick_mcp.configure_session(
                session_key,
                api_key,
                llm_model=_zeroclick_llm_model(),
                user_id=session_key,
                user_session_id=session_id or session_key,
                user_locale=request.headers.get("accept-language", "en-US").split(",")[0].strip() or "en-US",
                grouping_id="buyer-web-chat",
                user_ip=_client_ip(request),
                user_agent=request.headers.get("user-agent", ""),
            )
            await _zeroclick_mcp.ensure_initialized(session_key, api_key)
        status = _zeroclick_mcp.get_status(session_key)
        status["mcp_enabled"] = _zeroclick_mcp_enabled()
        return JSONResponse(content=status)
    except Exception as exc:
        return JSONResponse(
            content={
                "connected": False,
                "mcp_enabled": _zeroclick_mcp_enabled(),
                "error": str(exc),
            },
            status_code=502,
        )


@app.get("/ping")
async def ping():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/logs/stream")
async def log_stream(request: Request):
    """Stream log entries via SSE (broadcast to each subscriber)."""
    sub_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    _log_subscribers.add(sub_queue)

    async def event_generator():
        try:
            # Replay history so new connections see past events
            for entry in _log_history:
                yield {"event": "log", "data": json.dumps(entry)}
            # Stream live events
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(sub_queue.get(), timeout=15.0)
                    yield {"event": "log", "data": json.dumps(entry)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            _log_subscribers.discard(sub_queue)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Ledger / ROI endpoints
# ---------------------------------------------------------------------------


@app.get("/api/ledger")
async def get_ledger():
    """Return purchase ledger summary with ROI and seller stats."""
    return JSONResponse(content=ledger.get_summary())


@app.get("/api/ledger/records")
async def get_ledger_records():
    """Return all purchase records with evaluations."""
    from dataclasses import asdict
    records = ledger.get_all_records()
    return JSONResponse(content=[asdict(r) for r in records])


@app.get("/api/ledger/comparisons")
async def get_task_comparisons():
    """Return task-level two-seller comparison memory."""
    return JSONResponse(content=comparison_memory.list_all())


# ---------------------------------------------------------------------------
# A2A registration routes
# ---------------------------------------------------------------------------

# A2A registration routes (always mounted so sellers can register)
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

executor = RegistrationExecutor(seller_registry)
agent_card = _build_buyer_agent_card(BUYER_PORT)
task_store = InMemoryTaskStore()
handler = DefaultRequestHandler(
    agent_executor=executor,
    task_store=task_store,
)
a2a_app = A2AFastAPIApplication(
    agent_card=agent_card,
    http_handler=handler,
)
a2a_app.add_routes_to_app(app)

# ---------------------------------------------------------------------------
# Static file serving (production frontend)
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        """Serve the SPA index.html for all non-API routes."""
        file_path = (FRONTEND_DIR / path).resolve()
        if file_path.is_relative_to(FRONTEND_DIR.resolve()) and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")


def main():
    """Run the buyer agent web server."""
    import uvicorn

    mode = os.getenv("BUYER_AGENT_MODE", "smart")
    log(_logger, "WEB", "STARTUP", f"port={BUYER_PORT} mode={mode}")
    print(f"Buyer Agent Web Server running on http://localhost:{BUYER_PORT}")
    print(f"Mode: {mode} (smart buyer with ROI tracking)")
    print(f"A2A registration endpoint active")
    if FRONTEND_DIR.exists():
        print(f"Serving frontend from {FRONTEND_DIR}")
    else:
        print(f"Frontend not built — use http://localhost:5173 for dev")

    uvicorn.run(app, host="0.0.0.0", port=BUYER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
