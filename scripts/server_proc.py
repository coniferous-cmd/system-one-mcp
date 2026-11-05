"""Spawn and stop the server binary so tests can control its configuration."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ServerProcess:
    def __init__(
        self,
        env_overrides: dict[str, str] | None = None,
        binary: str | None = None,
        port: int | None = None,
        inherit_upstream: bool = False,
    ) -> None:
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self.binary = binary or default_binary()
        # Ambient SYSTEM_ONE_* values are scrubbed by default so a stray .env
        # cannot silently change what a test is measuring. Suites that *want*
        # the real upstream config opt in.
        if inherit_upstream:
            env = dict(os.environ)
        else:
            env = {k: v for k, v in os.environ.items() if not k.startswith("SYSTEM_ONE_")}
        env.setdefault("RUST_LOG", "warn")
        env.update(
            {
                "MCP_BIND_ADDR": f"127.0.0.1:{self.port}",
                "MCP_ALLOWED_HOSTS": "127.0.0.1,localhost",
            }
        )
        if env_overrides:
            env.update(env_overrides)
        self.env = env
        self.process: subprocess.Popen[bytes] | None = None
        # Logs go to a file rather than a pipe: a chatty run (RUST_LOG=trace)
        # would otherwise fill the pipe buffer and deadlock the server.
        self._log = tempfile.NamedTemporaryFile(
            prefix="system-one-mcp-", suffix=".log", delete=False
        )

    def __enter__(self) -> "ServerProcess":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()

    def start(self, timeout: float = 30.0) -> None:
        self.process = subprocess.Popen(
            [self.binary],
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"server exited with {self.process.returncode}: {self.output()}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError(f"server did not listen on port {self.port}")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)

    def wait_for_exit(self, timeout: float = 20.0) -> int:
        assert self.process is not None, "start() was not called"
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.stop()
            raise AssertionError(
                f"server should have exited but was still running: {self.output()}"
            ) from None

    def output(self) -> str:
        """Everything the server has written so far, running or not."""
        try:
            with open(self._log.name, "rb") as handle:
                return handle.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def cleanup(self) -> None:
        self.stop()
        try:
            os.unlink(self._log.name)
        except OSError:
            pass


def default_binary() -> str:
    name = "system-one-mcp" + (".exe" if os.name == "nt" else "")
    for profile in ("debug", "release"):
        candidate = os.path.join(ROOT, "target", profile, name)
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        "server binary not found; run `cargo build` first "
        f"(looked in {os.path.join(ROOT, 'target', 'debug')})"
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until_up(url: str, timeout: float = 10.0) -> None:
    """Poll the endpoint until it answers anything at all."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(
                urllib.request.Request(url, data=b"{}", method="POST"), timeout=2
            ).close()
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"{url} did not become reachable")


if __name__ == "__main__":
    with ServerProcess() as server:
        print(f"started {server.binary} on {server.url}")
        wait_until_up(server.url)
        print("reachable")
    sys.exit(0)
