"""Shared helpers for SafeLibs Docker tooling."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def repo_root() -> Path:
    """Return the repository root."""

    return REPO_ROOT


def resolve_repo_path(path: str | Path) -> Path:
    """Resolve a path against the repository root when it is relative."""

    resolved = Path(path)
    if resolved.is_absolute():
        return resolved
    return REPO_ROOT / resolved


def repo_relative_path(path: str | Path) -> str:
    """Return a POSIX-style path relative to the repository root."""

    resolved = resolve_repo_path(path).resolve()
    return resolved.relative_to(REPO_ROOT.resolve()).as_posix()


def ensure_directory(path: str | Path) -> Path:
    """Create a directory if it does not already exist."""

    directory = resolve_repo_path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_json(path: str | Path) -> Any:
    """Read JSON from disk using UTF-8."""

    with resolve_repo_path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, payload: Any) -> Path:
    """Write deterministically formatted JSON and return the resolved path."""

    destination = resolve_repo_path(path)
    ensure_directory(destination.parent)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
    return destination


def sha256_file(path: str | Path) -> str:
    """Compute the SHA-256 digest for a file."""

    digest = hashlib.sha256()
    with resolve_repo_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(argv: list[str]) -> str:
    """Run a subprocess and return stdout, raising on failure."""

    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        output = completed.stderr.strip() or completed.stdout.strip() or "no output"
        command = " ".join(argv)
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {command}: {output}")
    return completed.stdout
