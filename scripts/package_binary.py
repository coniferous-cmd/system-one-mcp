"""Package a release binary and write a SHA-256 checksum (standard library only)."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
TARGETS = (
    "x86_64-pc-windows-msvc",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-unknown-linux-musl",
    "aarch64-unknown-linux-musl",
)


def package(target: str, root: Path = ROOT) -> Path:
    if target not in TARGETS:
        raise ValueError(f"unsupported target: {target}")
    windows = target.endswith("windows-msvc")
    binary_name = "decision-mcp" + (".exe" if windows else "")
    binary = root / "target" / target / "release" / binary_name
    if not binary.is_file():
        raise FileNotFoundError(f"release binary not found: {binary}")

    dist = root / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    name = f"decision-mcp-{target}"
    archive = dist / (name + (".zip" if windows else ".tar.gz"))
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / name
        staging.mkdir()
        shutil.copy2(binary, staging / binary_name)
        if not windows:
            (staging / binary_name).chmod(0o755)
        for document in ("README.md", ".env.example"):
            shutil.copy2(root / document, staging / document)
        if windows:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                for path in sorted(staging.iterdir()):
                    output.write(path, f"{name}/{path.name}")
        else:
            def archive_permissions(member: tarfile.TarInfo) -> tarfile.TarInfo:
                # Set tar permissions explicitly, even when packaging on Windows.
                member.mode = 0o755 if member.isdir() or member.name == f"{name}/{binary_name}" else 0o644
                return member

            with tarfile.open(archive, "w:gz") as output:
                output.add(staging, arcname=name, filter=archive_permissions)

    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=TARGETS)
    args = parser.parse_args()
    print(package(args.target))


if __name__ == "__main__":
    main()
