"""Download and lock SafeLibs port Debian packages."""

from __future__ import annotations

import argparse
import copy
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import (
    ensure_directory,
    read_json,
    repo_relative_path,
    run_checked,
    sha256_file,
    write_json,
)

DEFAULT_SELECTION_MANIFEST = Path("dist/validator-selection.json")
DEFAULT_OUTPUT_ROOT = Path(".work/debs/port")
DEFAULT_OUTPUT = Path("dist/port-debs-lock.json")
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}
ALLOWED_ARCHITECTURES = {"amd64", "all"}


def build_release_asset_url(port_repository: str, port_release_tag: str, filename: str) -> str:
    """Construct the GitHub Releases asset URL from validator metadata."""

    return (
        f"https://github.com/{port_repository}/releases/download/"
        f"{port_release_tag}/{filename}"
    )


def download_file(url: str, destination: Path) -> None:
    """Download a file to the requested destination path."""

    request = urllib.request.Request(url, headers={"User-Agent": "safelibs-docker"})
    with urllib.request.urlopen(request, timeout=30) as response:
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)


def inspect_deb(path: Path) -> dict:
    """Read package metadata from a Debian archive."""

    output = run_checked(
        ["dpkg-deb", "--field", str(path), "Package", "Version", "Architecture"]
    )
    values: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped:
            _, value = stripped.split(":", 1)
            values.append(value.strip())
        else:
            values.append(stripped)
    if len(values) != 3:
        raise ValueError(f"Unexpected dpkg-deb field output for {path}: {output!r}")
    return {
        "package": values[0],
        "version": values[1],
        "architecture": values[2],
    }


def _validate_path_component(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected non-empty string for {field_name}.")
    if value in {".", ".."}:
        raise ValueError(f"Unsafe {field_name} {value!r}: dot path components are not allowed.")
    if "/" in value or "\\" in value:
        raise ValueError(
            f"Unsafe {field_name} {value!r}: nested or absolute path components are not allowed."
        )
    return value


def _build_destination_path(output_root: Path, library_name: str, filename: str) -> Path:
    safe_library_name = _validate_path_component(library_name, "library")
    safe_filename = _validate_path_component(filename, "filename")
    resolved_output_root = output_root.resolve()
    destination = (resolved_output_root / safe_library_name / safe_filename).resolve()
    try:
        destination.relative_to(resolved_output_root)
    except ValueError as exc:
        raise ValueError(
            f"Unsafe output path for library {safe_library_name!r} and filename {safe_filename!r}."
        ) from exc
    return destination


def _verify_digest_and_size(path: Path, deb_entry: dict) -> None:
    expected_sha = deb_entry["sha256"]
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha:
        raise ValueError(
            f"SHA-256 mismatch for {path}: expected {expected_sha}, observed {actual_sha}"
        )

    expected_size = deb_entry.get("size")
    if expected_size is not None:
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise ValueError(
                f"Size mismatch for {path}: expected {expected_size}, observed {actual_size}"
            )


def _matches_local_cache(path: Path, deb_entry: dict) -> bool:
    if not path.exists():
        return False

    expected_size = deb_entry.get("size")
    if expected_size is not None and path.stat().st_size != expected_size:
        return False

    return sha256_file(path) == deb_entry["sha256"]


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP_CODES
    return isinstance(exc, (TimeoutError, urllib.error.URLError, OSError))


def _download_with_retries(url: str, destination: Path) -> None:
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            download_file(url, destination)
            return
        except Exception as exc:
            destination.unlink(missing_ok=True)
            if attempt == attempts or not _is_retryable_error(exc):
                raise RuntimeError(f"Unable to download {url}: {exc}") from exc
            time.sleep(attempt)


def _materialize_deb(path: Path, source_url: str, deb_entry: dict) -> None:
    ensure_directory(path.parent)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        _download_with_retries(source_url, temp_path)
        _verify_digest_and_size(temp_path, deb_entry)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _lock_single_deb(library_entry: dict, output_root: Path, deb_entry: dict) -> dict:
    library_name = _validate_path_component(library_entry["library"], "library")
    filename = _validate_path_component(deb_entry["filename"], "filename")
    destination = _build_destination_path(output_root, library_name, filename)
    source_url = build_release_asset_url(
        library_entry["port_repository"],
        library_entry["port_release_tag"],
        filename,
    )

    if not _matches_local_cache(destination, deb_entry):
        _materialize_deb(destination, source_url, deb_entry)

    _verify_digest_and_size(destination, deb_entry)
    metadata = inspect_deb(destination)
    if metadata["package"] != deb_entry["package"]:
        raise ValueError(
            f"Package mismatch for {destination}: expected {deb_entry['package']}, "
            f"observed {metadata['package']}"
        )
    if metadata["architecture"] not in ALLOWED_ARCHITECTURES:
        raise ValueError(
            f"Unsupported architecture for {destination}: {metadata['architecture']}"
        )
    validator_architecture = deb_entry.get("architecture")
    if validator_architecture and metadata["architecture"] != validator_architecture:
        raise ValueError(
            f"Architecture mismatch for {destination}: expected {validator_architecture}, "
            f"observed {metadata['architecture']}"
        )

    return {
        "package": metadata["package"],
        "version": metadata["version"],
        "architecture": metadata["architecture"],
        "filename": filename,
        "sha256": deb_entry["sha256"],
        "size": deb_entry.get("size", destination.stat().st_size),
        "source_url": source_url,
        "local_path": repo_relative_path(destination),
    }


def lock_library_debs(library_entry: dict, output_root: Path) -> dict:
    """Materialize and lock every expected Debian package for a library."""

    library_name = _validate_path_component(library_entry["library"], "library")
    locked_library = {
        "library": library_name,
        "apt_packages": copy.deepcopy(library_entry["apt_packages"]),
        "totals": copy.deepcopy(library_entry["totals"]),
        "port_repository": library_entry["port_repository"],
        "port_tag_ref": library_entry["port_tag_ref"],
        "port_commit": library_entry["port_commit"],
        "port_release_tag": library_entry["port_release_tag"],
        "unported_original_packages": copy.deepcopy(
            library_entry["unported_original_packages"]
        ),
        "port_debs": [],
    }
    for deb_entry in library_entry.get("port_debs") or []:
        locked_library["port_debs"].append(_lock_single_deb(library_entry, output_root, deb_entry))
    return locked_library


def build_port_deb_lock(selection_manifest: dict, output_root: Path) -> dict:
    """Build the lock manifest from the resolved validator selection."""

    return {
        "site_url": selection_manifest["site_url"],
        "schema_version": selection_manifest["schema_version"],
        "proof_version": selection_manifest["proof_version"],
        "selected_mode": selection_manifest["selected_mode"],
        "suite": copy.deepcopy(selection_manifest["suite"]),
        "proof_totals": copy.deepcopy(selection_manifest["proof_totals"]),
        "selection_scope": selection_manifest["selection_scope"],
        "requested_libraries": list(selection_manifest["requested_libraries"]),
        "libraries": [
            lock_library_debs(library_entry, output_root)
            for library_entry in selection_manifest.get("libraries") or []
        ],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, default=DEFAULT_SELECTION_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        selection_manifest = read_json(args.selection_manifest)
        lock_manifest = build_port_deb_lock(selection_manifest, args.output_root)
        output_path = write_json(args.output, lock_manifest)
    except Exception as exc:  # pragma: no cover - CLI surface
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {output_path} with {sum(len(lib['port_debs']) for lib in lock_manifest['libraries'])} "
        f"locked Debian packages.",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
