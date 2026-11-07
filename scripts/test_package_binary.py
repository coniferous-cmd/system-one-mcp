"""Offline tests for release archives and explicit test binary selection."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from package_binary import TARGETS, package
from server_proc import default_binary


class PackageTests(unittest.TestCase):
    def test_archives_and_checksums(self) -> None:
        for target in TARGETS:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                windows = target.endswith("windows-msvc")
                binary_name = "decision-mcp" + (".exe" if windows else "")
                binary = root / "target" / target / "release" / binary_name
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b"test release binary")
                for name in ("README.md", ".env.example"):
                    (root / name).write_text("test document", encoding="utf-8")
                archive = package(target, root)
                prefix = f"decision-mcp-{target}/"
                expected = {prefix + name for name in (binary_name, "README.md", ".env.example")}
                if windows:
                    with zipfile.ZipFile(archive) as handle:
                        self.assertEqual(set(handle.namelist()), expected)
                        self.assertEqual(handle.read(prefix + binary_name), binary.read_bytes())
                else:
                    with tarfile.open(archive) as handle:
                        self.assertEqual({m.name for m in handle if m.isfile()}, expected)
                        member = handle.getmember(prefix + binary_name)
                        self.assertEqual(member.mode & 0o777, 0o755)
                        self.assertEqual(handle.extractfile(member).read(), binary.read_bytes())
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                self.assertEqual(
                    archive.with_name(archive.name + ".sha256").read_text(encoding="utf-8"),
                    f"{digest}  {archive.name}\n",
                )

    def test_missing_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                package(TARGETS[0], Path(temporary))

    def test_unsupported_target(self) -> None:
        with self.assertRaises(ValueError):
            package("not-a-target")

    def test_explicit_test_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "server"
            binary.touch()
            with patch.dict(os.environ, {"MCP_TEST_BINARY": str(binary)}):
                self.assertEqual(default_binary(), str(binary.resolve()))
                binary.unlink()
                with self.assertRaises(FileNotFoundError):
                    default_binary()


if __name__ == "__main__":
    unittest.main()
