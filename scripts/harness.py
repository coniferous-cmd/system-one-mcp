"""Tiny assertion harness so the test scripts stay dependency-free."""

from __future__ import annotations

import sys
import time
import traceback
from typing import Any, Callable

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


class Runner:
    def __init__(self, title: str) -> None:
        self.title = title
        self.passed = 0
        self.failed: list[str] = []
        self.skipped = 0
        self._started = time.monotonic()

    def section(self, name: str) -> None:
        print(f"\n{DIM}──{RESET} {name}")

    def check(self, name: str, condition: bool, detail: Any = None) -> bool:
        if condition:
            self.passed += 1
            print(f"  {GREEN}PASS{RESET} {name}")
        else:
            self.failed.append(name)
            print(f"  {RED}FAIL{RESET} {name}")
            if detail is not None:
                print(f"       {DIM}{_short(detail)}{RESET}")
        return bool(condition)

    def equal(self, name: str, actual: Any, expected: Any) -> bool:
        return self.check(name, actual == expected, f"actual={actual!r} expected={expected!r}")

    def skip(self, name: str, reason: str) -> None:
        self.skipped += 1
        print(f"  {DIM}SKIP{RESET} {name} ({reason})")

    def case(self, name: str, fn: Callable[[], None]) -> None:
        """Run one case; an unexpected exception fails only that case."""
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a test case may fail anyhow
            self.failed.append(name)
            print(f"  {RED}FAIL{RESET} {name} raised {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)

    def finish(self) -> int:
        elapsed = time.monotonic() - self._started
        total = self.passed + len(self.failed)
        print(f"\n{self.title}: {self.passed}/{total} passed", end="")
        if self.skipped:
            print(f", {self.skipped} skipped", end="")
        print(f" in {elapsed:.1f}s")
        if self.failed:
            print(f"{RED}failures:{RESET}")
            for name in self.failed:
                print(f"  - {name}")
            return 1
        print(f"{GREEN}all checks passed{RESET}")
        return 0


def _short(value: Any, limit: int = 400) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def fail(message: str) -> None:
    raise AssertionError(message)


def tool_error_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Pull the machine-readable error out of a tool result, or fail loudly."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and "error" in structured:
        return structured["error"]
    fail(f"expected a structured error payload, got {_short(result)}")
    raise AssertionError("unreachable")


def tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    fail(f"expected structuredContent, got {_short(result)}")
    raise AssertionError("unreachable")


def exit_on_error(runner: Runner) -> None:
    sys.exit(runner.finish())
