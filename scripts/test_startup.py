"""Startup and security behaviour, driven by launching the binary directly.

Covers what protocol tests cannot: refusal to boot on missing configuration,
host allow-listing, and that the API key never reaches the logs.

    python scripts/test_startup.py
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Runner, exit_on_error  # noqa: E402
from server_proc import ServerProcess, wait_until_up  # noqa: E402

SECRET = "sk-test-do-not-log-me"


def main() -> None:
    runner = Runner("startup")

    runner.section("missing configuration aborts startup")
    runner.case(
        "no SYSTEM_ONE_API_KEY",
        lambda: _expect_boot_failure(runner, {"SYSTEM_ONE_MODEL": "m"}, "SYSTEM_ONE_API_KEY"),
    )
    runner.case(
        "no SYSTEM_ONE_MODEL",
        lambda: _expect_boot_failure(runner, {"SYSTEM_ONE_API_KEY": SECRET}, "SYSTEM_ONE_MODEL"),
    )
    runner.case(
        "blank SYSTEM_ONE_API_KEY",
        lambda: _expect_boot_failure(
            runner,
            {"SYSTEM_ONE_API_KEY": "   ", "SYSTEM_ONE_MODEL": "m"},
            "SYSTEM_ONE_API_KEY",
        ),
    )
    runner.case(
        "non-http endpoint rejected",
        lambda: _expect_boot_failure(
            runner,
            {
                "SYSTEM_ONE_API_KEY": SECRET,
                "SYSTEM_ONE_MODEL": "m",
                "SYSTEM_ONE_ENDPOINT": "ftp://example.com/x",
            },
            "SYSTEM_ONE_ENDPOINT",
        ),
    )

    runner.section("host allow-list guards against DNS rebinding")
    with ServerProcess(
        {"SYSTEM_ONE_API_KEY": SECRET, "SYSTEM_ONE_MODEL": "m"}, port=None
    ) as server:
        wait_until_up(server.url)

        status = _initialize(server.url, host="127.0.0.1")
        runner.equal("loopback Host accepted", status, 200)

        status = _initialize(server.url, host=f"127.0.0.1:{server.port}")
        runner.equal("Host with port accepted", status, 200)

        status = _initialize(server.url, host="evil.example.com")
        runner.equal("foreign Host rejected", status, 403)

    runner.section("API key stays out of the logs and the tool surface")
    with ServerProcess(
        {
            "SYSTEM_ONE_API_KEY": SECRET,
            "SYSTEM_ONE_MODEL": "m",
            "RUST_LOG": "trace",
            "SYSTEM_ONE_ENDPOINT": f"http://127.0.0.1:{_free_port()}/v1/systemone",
        }
    ) as server:
        wait_until_up(server.url)
        from mcp_client import McpClient

        with McpClient(server.url) as client:
            client.initialize()
            tools = client.list_tools()
            # A wrong-port endpoint fails fast, which also exercises the error
            # path; the response must not echo the credential.
            result = client.call_tool(
                "Noul",
                {
                    "state": "probe",
                    "questions": [{"id": "q", "instructions": "?"}],
                },
            )
            dumped = str(result)
            runner.check("error result present", result.get("isError") is True, result)
            runner.check("key not echoed in tool result", SECRET not in dumped)
            runner.check(
                "key not exposed as a tool parameter",
                all("api_key" not in (t.get("inputSchema") or {}).get("properties", {})
                    for t in tools),
            )

    runner.section("log output")
    with ServerProcess(
        {"SYSTEM_ONE_API_KEY": SECRET, "SYSTEM_ONE_MODEL": "m", "RUST_LOG": "info"}
    ) as server:
        wait_until_up(server.url)
        server.stop()
        log = server.output()
        runner.check("startup line logged", "listening on" in log, log)
        runner.check("key absent from stdout/stderr", SECRET not in log, log)

    exit_on_error(runner)


def _expect_boot_failure(runner: Runner, env: dict[str, str], needle: str) -> None:
    server = ServerProcess(env)
    try:
        server.start(timeout=15)
    except RuntimeError:
        code = server.wait_for_exit()
        runner.check(f"exit code is non-zero ({code})", code != 0)
        output = server.output()
        runner.check(f"error names {needle}", needle in output, output)
        return
    server.stop()
    runner.check(f"startup should fail without {needle}", False, "server started anyway")


def _initialize(url: str, host: str) -> int:
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        b'{"protocolVersion":"2025-06-18","capabilities":{},'
        b'"clientInfo":{"name":"selftest","version":"1"}}}'
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Host": host,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _free_port() -> int:
    from server_proc import free_port

    return free_port()


if __name__ == "__main__":
    main()
