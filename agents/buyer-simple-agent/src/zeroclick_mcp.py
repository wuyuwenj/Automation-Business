"""Lightweight MCP client for the ZeroClick signal server."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any

import httpx


ZEROCLICK_MCP_URL = "https://zeroclick.dev/mcp/v2"
MCP_PROTOCOL_VERSION = "2024-11-05"


@dataclass
class MCPToolSummary:
    """Subset of MCP tool metadata useful for diagnostics."""

    name: str
    description: str = ""
    inputSchema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPSessionState:
    """Per-session ZeroClick MCP connection state."""

    session_key: str
    api_key: str
    mcp_session_id: str = ""
    initialized: bool = False
    initialized_notified: bool = False
    tools: list[MCPToolSummary] = field(default_factory=list)
    last_error: str = ""
    last_signal_result: str = ""
    protocol_version: str = MCP_PROTOCOL_VERSION
    llm_model: str = "openai/gpt-4o"
    user_id: str = ""
    user_session_id: str = ""
    user_locale: str = "en-US"
    grouping_id: str = "buyer-web-chat"
    user_ip: str = ""
    user_agent: str = ""


class ZeroClickMCPClient:
    """Minimal JSON-RPC MCP client over HTTP for ZeroClick signals."""

    def __init__(self, base_url: str = ZEROCLICK_MCP_URL):
        self._base_url = base_url.rstrip("/")
        self._lock = asyncio.Lock()
        self._sessions: dict[str, MCPSessionState] = {}

    def get_status(self, session_key: str | None = None) -> dict[str, Any]:
        """Return MCP session diagnostics for one or all sessions."""
        if session_key:
            state = self._sessions.get(session_key)
            if not state:
                return {"connected": False, "session_key": session_key}
            return self._serialize_state(state)
        return {
            "connected_sessions": len(self._sessions),
            "sessions": [self._serialize_state(state) for state in self._sessions.values()],
        }

    async def ensure_initialized(self, session_key: str, api_key: str) -> MCPSessionState:
        """Initialize one MCP session if needed and cache tool metadata."""
        async with self._lock:
            state = self._sessions.get(session_key)
            if state and state.initialized and state.api_key == api_key:
                return state

            state = state or MCPSessionState(session_key=session_key, api_key=api_key)
            state.api_key = api_key
            self._sessions[session_key] = state

        init_payload = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": "buyer-agent-web",
                "version": "1.0.0",
            },
        }

        _, init_result, init_headers = await self._rpc(
            state,
            method="initialize",
            params=init_payload,
            rpc_id=1,
            include_protocol_header=False,
        )
        negotiated_protocol = (init_result.get("protocolVersion") or "").strip()
        if negotiated_protocol:
            state.protocol_version = negotiated_protocol

        # Some MCP servers issue a session header that must be reused.
        returned_session = (
            init_headers.get("mcp-session-id")
            or init_headers.get("Mcp-Session-Id")
            or init_headers.get("x-mcp-session-id")
        )
        if returned_session:
            state.mcp_session_id = returned_session

        await self._rpc(
            state,
            method="notifications/initialized",
            params={},
            rpc_id=None,
        )
        state.initialized = True
        state.initialized_notified = True
        state.last_error = ""

        tools = await self.list_tools(session_key, api_key)
        state.tools = tools
        return state

    async def list_tools(self, session_key: str, api_key: str) -> list[MCPToolSummary]:
        """Fetch tool metadata for a session."""
        state = self._sessions.get(session_key) or MCPSessionState(session_key=session_key, api_key=api_key)
        state.api_key = api_key
        self._sessions[session_key] = state

        _, result, _ = await self._rpc(
            state,
            method="tools/list",
            params={},
            rpc_id=2,
        )
        tools = []
        for item in result.get("tools", []):
            tools.append(MCPToolSummary(
                name=item.get("name", ""),
                description=item.get("description", ""),
                inputSchema=item.get("inputSchema", {}) or {},
            ))
        state.tools = tools
        return tools

    def configure_session(
        self,
        session_key: str,
        api_key: str,
        *,
        llm_model: str,
        user_id: str,
        user_session_id: str,
        user_locale: str,
        grouping_id: str,
        user_ip: str,
        user_agent: str,
    ) -> MCPSessionState:
        """Attach request/user context required by the ZeroClick MCP server."""
        state = self._sessions.get(session_key) or MCPSessionState(session_key=session_key, api_key=api_key)
        state.api_key = api_key
        state.llm_model = llm_model
        state.user_id = user_id
        state.user_session_id = user_session_id
        state.user_locale = user_locale
        state.grouping_id = grouping_id
        state.user_ip = user_ip
        state.user_agent = user_agent
        self._sessions[session_key] = state
        return state

    async def broadcast_signal(self, session_key: str, api_key: str, signals: list[dict[str, Any]]) -> dict[str, Any]:
        """Call ZeroClick's broadcast_signal MCP tool."""
        state = await self.ensure_initialized(session_key, api_key)
        _, result, _ = await self._rpc(
            state,
            method="tools/call",
            params={
                "name": "broadcast_signal",
                "arguments": {"signals": signals},
            },
            rpc_id=3,
        )
        text = ""
        for item in result.get("content", []):
            if item.get("type") == "text":
                text = item.get("text", "")
                break
        state.last_signal_result = text or "ok"
        state.last_error = ""
        return result

    async def _rpc(
        self,
        state: MCPSessionState,
        method: str,
        params: dict[str, Any],
        rpc_id: int | None,
        *,
        include_protocol_header: bool = True,
    ) -> tuple[int, dict[str, Any], httpx.Headers]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "x-zc-api-key": state.api_key,
            "x-zc-llm-model": state.llm_model,
        }
        if include_protocol_header and state.protocol_version:
            headers["mcp-protocol-version"] = state.protocol_version
        if state.mcp_session_id:
            headers["mcp-session-id"] = state.mcp_session_id
        if state.user_id:
            headers["x-zc-user-id"] = state.user_id
        if state.user_session_id:
            headers["x-zc-user-session-id"] = state.user_session_id
        if state.user_locale:
            headers["x-zc-user-locale"] = state.user_locale
        if state.grouping_id:
            headers["x-zc-grouping-id"] = state.grouping_id
        if state.user_ip:
            headers["x-zc-user-ip"] = state.user_ip
        if state.user_agent:
            headers["x-zc-user-agent"] = state.user_agent[:1000]

        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if rpc_id is not None:
            body["id"] = rpc_id

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self._base_url, headers=headers, json=body)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                state.last_error = f"HTTP {exc.response.status_code}: {detail}"
                raise RuntimeError(
                    f"MCP {method} failed with HTTP {exc.response.status_code}: {detail}"
                ) from exc

            if rpc_id is None:
                return resp.status_code, {}, resp.headers

            payload = self._parse_response_payload(resp, rpc_id)

        if "error" in payload:
            message = payload["error"].get("message", "Unknown MCP error")
            state.last_error = message
            raise RuntimeError(f"MCP {method} failed: {message}")

        return resp.status_code, payload.get("result", {}), resp.headers

    @staticmethod
    def _parse_response_payload(resp: httpx.Response, rpc_id: int) -> dict[str, Any]:
        content_type = resp.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            return ZeroClickMCPClient._parse_sse_payload(resp.text, rpc_id)
        if not resp.text.strip():
            raise RuntimeError(
                f"MCP request returned an empty {content_type or 'response'} body"
            )
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            preview = resp.text[:500]
            raise RuntimeError(
                f"MCP response was not valid JSON (content-type={content_type or 'unknown'}): {preview}"
            ) from exc

    @staticmethod
    def _parse_sse_payload(body: str, rpc_id: int) -> dict[str, Any]:
        data_chunks: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_chunks.append(line[5:].lstrip())

        if not data_chunks:
            preview = body[:500]
            raise RuntimeError(f"MCP SSE response did not include any data frames: {preview}")

        for chunk in data_chunks:
            try:
                payload = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if "id" in payload and payload.get("id") != rpc_id:
                continue
            return payload

        preview = " | ".join(data_chunks[:3])[:500]
        raise RuntimeError(f"MCP SSE response did not include a JSON-RPC result for id={rpc_id}: {preview}")

    @staticmethod
    def _serialize_state(state: MCPSessionState) -> dict[str, Any]:
        return {
            "session_key": state.session_key,
            "initialized": state.initialized,
            "initialized_notified": state.initialized_notified,
            "mcp_session_id_present": bool(state.mcp_session_id),
            "protocol_version": state.protocol_version,
            "llm_model": state.llm_model,
            "user_id_present": bool(state.user_id),
            "user_session_id_present": bool(state.user_session_id),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                }
                for tool in state.tools
            ],
            "last_signal_result": state.last_signal_result,
            "last_error": state.last_error,
        }
