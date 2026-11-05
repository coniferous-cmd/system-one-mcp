"""Contract tests: tool surface and argument validation.

These never reach the upstream API, so they are free to run and work even with a
placeholder API key.

    python scripts/test_protocol.py
    MCP_URL=http://127.0.0.1:8080/mcp python scripts/test_protocol.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Runner, exit_on_error, tool_error_payload  # noqa: E402
from mcp_client import McpClient, McpError  # noqa: E402

URL = os.environ.get("MCP_URL", "http://127.0.0.1:8080/mcp")
TOOLS = ("Choice", "Score", "Noul")

STATE = "客户反馈：同一笔订单被扣款两次，希望今天内回复。"


def main() -> None:
    runner = Runner("protocol")

    with McpClient(URL) as client:
        runner.section("initialize")
        info = client.initialize()
        runner.equal("protocolVersion echoed", info.get("protocolVersion"), "2025-06-18")
        server_info = info.get("serverInfo") or {}
        runner.equal("serverInfo.name", server_info.get("name"), "system-one-mcp")
        runner.check("serverInfo.version present", bool(server_info.get("version")))
        runner.check(
            "tools capability advertised", "tools" in (info.get("capabilities") or {})
        )
        runner.check("instructions provided", bool(info.get("instructions")))

        runner.section("tools/list")
        tools = {tool["name"]: tool for tool in client.list_tools()}
        runner.equal("exactly three tools", sorted(tools), sorted(TOOLS))

        for name in TOOLS:
            tool = tools.get(name, {})
            schema = tool.get("inputSchema") or {}
            props = schema.get("properties") or {}
            required = schema.get("required") or []
            runner.check(f"{name}: has description", bool(tool.get("description")))
            runner.check(
                f"{name}: declares state + questions",
                {"state", "questions"} <= set(props),
            )
            runner.equal(
                f"{name}: required fields", sorted(required), ["questions", "state"]
            )
            runner.check(
                f"{name}: idempotency_key optional",
                "idempotency_key" in props and "idempotency_key" not in required,
            )
            runner.check(
                f"{name}: no model parameter (server-side only)", "model" not in props
            )

        runner.section("schema validation (rejected before upstream)")
        runner.case(
            "unknown tool is a JSON-RPC error",
            lambda: _expect_rpc_error(client, "NoSuchTool", {"state": STATE}),
        )
        # Argument deserialization failures are reported as tool errors with a
        # readable message rather than as JSON-RPC errors.
        _expect_deserialize_error(
            runner,
            client,
            "missing questions rejected",
            "Choice",
            {"state": STATE},
            needle="questions",
        )
        _expect_deserialize_error(
            runner,
            client,
            "wrong criteria type rejected",
            "Score",
            {
                "state": STATE,
                "questions": [{"id": "u", "instructions": "?", "criteria": "flat"}],
            },
            needle="sequence",
        )

        runner.section("server-side validation (structured tool errors)")
        _expect_tool_error(
            runner,
            client,
            "numeric state rejected",
            "Choice",
            {
                "state": 42,
                "questions": [{"id": "q", "instructions": "?", "criteria": [{"name": "a"}]}],
            },
            code="invalid_params",
        )
        _expect_tool_error(
            runner,
            client,
            "empty questions rejected",
            "Noul",
            {"state": STATE, "questions": []},
            code="invalid_params",
        )
        _expect_tool_error(
            runner,
            client,
            "33 questions rejected",
            "Noul",
            {
                "state": STATE,
                "questions": [{"id": f"q{i}", "instructions": "?"} for i in range(33)],
            },
            code="invalid_params",
        )
        _expect_tool_error(
            runner,
            client,
            "duplicate ids rejected",
            "Noul",
            {
                "state": STATE,
                "questions": [
                    {"id": "same", "instructions": "?"},
                    {"id": "same", "instructions": "?"},
                ],
            },
            code="invalid_params",
        )

        # The 32-question boundary is exercised in test_live.py: proving the
        # guard accepts the maximum requires a real (billable) upstream call.

    exit_on_error(runner)


def _expect_deserialize_error(
    runner: Runner, client: McpClient, label: str, name: str, arguments: dict, needle: str
) -> None:
    def run() -> None:
        result = client.call_tool(name, arguments)
        runner.equal(f"{label}: isError", result.get("isError"), True)
        text = " ".join(
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        )
        runner.check(f"{label}: message mentions {needle!r}", needle in text, text)

    runner.case(label, run)


def _expect_rpc_error(client: McpClient, name: str, arguments: dict) -> None:
    try:
        result = client.call_tool(name, arguments)
    except McpError:
        return
    raise AssertionError(f"expected a JSON-RPC error, got {result}")


def _expect_tool_error(
    runner: Runner,
    client: McpClient,
    label: str,
    name: str,
    arguments: dict,
    code: str | None = None,
) -> None:
    def run() -> None:
        result = client.call_tool(name, arguments)
        runner.equal(
            f"{label}: isError",
            result.get("isError"),
            True,
        )
        payload = tool_error_payload(result)
        if code is not None:
            runner.equal(f"{label}: code", payload.get("code"), code)

    runner.case(label, run)


if __name__ == "__main__":
    main()
