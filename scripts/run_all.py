"""One command to run the whole suite.

    python scripts/run_all.py          # free: protocol + startup
    LIVE=1 python scripts/run_all.py   # adds billable upstream calls
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Order matters: the cheap, deterministic suites run first.
SUITES = [
    ("startup", "test_startup.py", False),
    ("protocol", "test_protocol.py", True),
    ("live", "test_live.py", True),
]


def main() -> None:
    live = os.environ.get("LIVE") == "1"
    if not live:
        log("live tests skipped (set LIVE=1 to include billable upstream calls)")

    results: list[tuple[str, int, float]] = []
    for name, script, needs_server in SUITES:
        if name == "live" and not live:
            continue
        log(f"running {name} …")
        started = time.monotonic()
        code = _run(name, script, needs_server, live)
        results.append((name, code, time.monotonic() - started))

    print()
    failed = [name for name, code, _ in results if code != 0]
    for name, code, elapsed in results:
        status = "ok  " if code == 0 else "FAIL"
        print(f"  {status} {name:<9} {elapsed:5.1f}s")
    print()
    if failed:
        print(f"failed: {', '.join(failed)}")
        sys.exit(1)
    print("suite passed")


def _run(name: str, script: str, needs_server: bool, live: bool) -> int:
    path = os.path.join(HERE, script)
    env = dict(os.environ)
    if live:
        env.setdefault("LIVE", "1")
    if not needs_server:
        return subprocess.call([sys.executable, path], env=env)

    # Protocol and live tests need a running server; startup tests manage their
    # own processes to control env vars.
    sys.path.insert(0, HERE)
    from server_proc import ServerProcess, wait_until_up

    # Forward the caller's upstream configuration so live tests hit the endpoint
    # the operator intended, not the built-in default.
    overrides = {
        key: value for key, value in env.items() if key.startswith("DECISION_")
    }
    if not overrides.get("DECISION_API_KEY"):
        log(f"{name}: DECISION_API_KEY not set, using a placeholder")
        overrides["DECISION_API_KEY"] = "placeholder"
    if not overrides.get("DECISION_MODEL"):
        overrides["DECISION_MODEL"] = "placeholder"

    try:
        with ServerProcess(overrides, inherit_upstream=True) as server:
            wait_until_up(server.url)
            env["MCP_URL"] = server.url
            print()
            return subprocess.call([sys.executable, path], env=env)
    except Exception as exc:  # noqa: BLE001 - surfaced as a suite failure
        log(f"{name}: could not start server: {exc}")
        return 1


def log(message: str) -> None:
    print(f"[run_all] {message}")


if __name__ == "__main__":
    main()
