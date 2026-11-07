"""Minimal MCP client over Streamable HTTP using only the standard library.

Why hand-rolled: the goal is to test *our* server's wire behaviour without
pulling in the official SDK, so any protocol drift shows up loudly instead of
being papered over by SDK convenience helpers.

Usage:
    from mcp_client import McpClient

    with McpClient("http://127.0.0.1:8080/mcp") as client:
        client.initialize()
        print(client.list_tools())
        print(client.call_tool("Noul", {"state": "...", "questions": [...]}))
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_PROTOCOL = "2025-06-18"


class McpError(RuntimeError):
    """Raised when the server returns a JSON-RPC error or an unreadable reply."""


class McpClient:
    def __init__(self, url: str, timeout: float = 180.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] | None = None
        self._next_id = 0

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Best-effort session teardown; servers may not implement DELETE."""
        if not self.session_id:
            return
        request = urllib.request.Request(
            self.url, method="DELETE", headers=self._headers()
        )
        try:
            urllib.request.urlopen(request, timeout=10).close()
        except Exception:
            pass
        finally:
            self.session_id = None

    # -- protocol ----------------------------------------------------------

    def initialize(self, protocol: str = DEFAULT_PROTOCOL) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": protocol,
                "capabilities": {},
                "clientInfo": {"name": "decision-mcp-selftest", "version": "1.0"},
            },
        )
        self.protocol_version = result.get("protocolVersion")
        self.server_info = result.get("serverInfo")
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        return self._rpc("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return the raw CallToolResult so tests can assert on isError/content."""
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    # -- transport ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
            headers["MCP-Protocol-Version"] = self.protocol_version or DEFAULT_PROTOCOL
        return headers

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload)

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        body, headers = self._post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        message = self._extract(body, headers, request_id)
        if "error" in message:
            error = message["error"]
            raise McpError(
                f"{method} failed: {error.get('code')} {error.get('message')}"
            )
        return message.get("result", {})

    def _post(
        self, payload: dict[str, Any], retry_on_404_session: bool = True
    ) -> tuple[bytes, dict[str, str]]:
        if os.environ.get("MCP_DEBUG"):
            print(
                f"    [mcp] -> {payload.get('method')} "
                f"id={payload.get('id')} session={self.session_id}",
                file=sys.stderr,
            )
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=data, method="POST", headers=self._headers()
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    self.session_id = session_id
                return response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            text = body.decode("utf-8", "replace")
            # A dropped session makes the transport answer 404; reopen one and
            # replay so a long test run survives server restarts.
            if exc.code == 404 and retry_on_404_session and self.session_id:
                print("[mcp] session lost, renegotiating", file=sys.stderr)
                self.session_id = None
                self._renegotiate_after_session_loss()
                return self._post(payload, retry_on_404_session=False)
            raise McpError(f"HTTP {exc.code} from {self.url}: {text}") from None

    def _renegotiate_after_session_loss(self) -> None:
        self._next_id += 1
        self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id + 1000,
                "method": "initialize",
                "params": {
                    "protocolVersion": DEFAULT_PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "decision-mcp-selftest", "version": "1.0"},
                },
            },
            retry_on_404_session=False,
        )
        self._notify("notifications/initialized")

    def _extract(
        self, body: bytes, headers: dict[str, str], request_id: int
    ) -> dict[str, Any]:
        content_type = ""
        for key, value in headers.items():
            if key.lower() == "content-type":
                content_type = value.lower()
                break

        text = body.decode("utf-8", "replace").strip()
        if not text:
            raise McpError(f"empty body for request {request_id}")

        # The server may answer with plain JSON or with an SSE stream depending
        # on negotiated capabilities, so handle both.
        candidates = (
            [text] if "event-stream" not in content_type else _sse_messages(text)
        )
        seen: list[Any] = []
        for candidate in candidates:
            if not candidate.strip():
                continue
            try:
                message = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                return message
            seen.append(message.get("id"))

        raise McpError(
            f"no JSON-RPC response with id={request_id} in reply "
            f"(content-type={content_type or 'unknown'}, ids seen={seen})"
        )


def _sse_messages(text: str) -> list[str]:
    """Split an SSE stream into the JSON payload of each event."""
    messages: list[str] = []
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
        elif not line.strip():
            if buffer:
                messages.append("\n".join(buffer))
                buffer = []
    if buffer:
        messages.append("\n".join(buffer))
    return messages
