"""Resolve the current fully passing SafeLibs validator selection."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import urllib.request
from pathlib import Path

from . import write_json

DEFAULT_SITE_URL = "https://safelibs.github.io/validator/site-data.json"
DEFAULT_MODE = "port"
DEFAULT_OUTPUT = Path("dist/validator-selection.json")
_RUNTIME_PACKAGE_SUFFIX_EXCLUDES = ("-dev", "-doc", "-tools", "-progs", "-utils", "-tests")
_RUNTIME_PACKAGE_PREFIX_EXCLUDES = ("gir1.2-", "python3-")


def _runtime_packages_fallback(apt_packages: list[str]) -> list[str]:
    """Mirror the validator runtime_packages heuristic.

    Used as a fallback when the validator output predates the
    runtime_packages field, so a freshly pushed validator does not have to
    finish republishing before docker rebuilds succeed.
    """

    runtime: list[str] = []
    for package in apt_packages or []:
        if not isinstance(package, str) or not package.startswith("lib"):
            continue
        if any(package.endswith(suffix) for suffix in _RUNTIME_PACKAGE_SUFFIX_EXCLUDES):
            continue
        if any(package.startswith(prefix) for prefix in _RUNTIME_PACKAGE_PREFIX_EXCLUDES):
            continue
        runtime.append(package)
    return runtime


def fetch_site_data(site_url: str) -> dict:
    """Fetch and validate the live validator payload."""

    request = urllib.request.Request(site_url, headers={"User-Agent": "safelibs-docker"})
    with urllib.request.urlopen(request, timeout=30) as response:
        site_data = json.load(response)
    if not isinstance(site_data, dict):
        raise ValueError("Validator site data must be a JSON object.")
    schema_version = site_data.get("schema_version")
    if schema_version != 2:
        raise ValueError(f"Unsupported validator schema_version {schema_version!r}; expected 2.")
    return site_data


def find_proof(site_data: dict, mode: str) -> dict:
    """Return the proof object for the requested mode."""

    for proof in site_data.get("proofs") or []:
        if proof.get("mode") == mode:
            proof_copy = copy.deepcopy(proof)
            proof_copy["schema_version"] = site_data["schema_version"]
            return proof_copy
    raise ValueError(f"Validator proof for mode {mode!r} was not found.")


def is_fully_passing(library_entry: dict) -> bool:
    """Return whether a library currently passes every validator case."""

    totals = library_entry.get("totals") or {}
    return totals.get("failed") == 0 and totals.get("passed") == totals.get("cases")


def select_libraries(proof: dict, requested_libraries: list[str]) -> list[dict]:
    """Select fully passing libraries, preserving validator order."""

    eligible_libraries = [copy.deepcopy(entry) for entry in proof.get("libraries") or [] if is_fully_passing(entry)]
    if not requested_libraries:
        return eligible_libraries

    eligible_by_name = {entry.get("library"): entry for entry in eligible_libraries}
    missing = [library for library in requested_libraries if library not in eligible_by_name]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(
            "Requested libraries are not present in the current fully passing selection: "
            f"{missing_list}"
        )

    requested_names = set(requested_libraries)
    return [entry for entry in eligible_libraries if entry.get("library") in requested_names]


def build_selection_manifest(site_url: str, proof: dict, requested_libraries: list[str]) -> dict:
    """Build the deterministic selection manifest."""

    selected_libraries = select_libraries(proof, requested_libraries)
    manifest_libraries = []
    for library_entry in selected_libraries:
        runtime_packages = library_entry.get("runtime_packages")
        if not isinstance(runtime_packages, list) or not runtime_packages:
            runtime_packages = _runtime_packages_fallback(library_entry.get("apt_packages") or [])
        if not runtime_packages:
            raise ValueError(
                f"library {library_entry.get('library')!r} has no runtime library packages; "
                "neither validator runtime_packages nor apt_packages yields a non-empty subset"
            )
        manifest_libraries.append(
            {
                "library": library_entry["library"],
                "apt_packages": copy.deepcopy(library_entry["apt_packages"]),
                "runtime_packages": list(runtime_packages),
                "totals": copy.deepcopy(library_entry["totals"]),
                "port_repository": library_entry["port_repository"],
                "port_tag_ref": library_entry["port_tag_ref"],
                "port_commit": library_entry["port_commit"],
                "port_release_tag": library_entry["port_release_tag"],
                "port_debs": copy.deepcopy(library_entry["port_debs"]),
                "unported_original_packages": copy.deepcopy(
                    library_entry["unported_original_packages"]
                ),
            }
        )

    return {
        "site_url": site_url,
        "schema_version": proof["schema_version"],
        "proof_version": proof["proof_version"],
        "selected_mode": proof["mode"],
        "suite": copy.deepcopy(proof["suite"]),
        "proof_totals": copy.deepcopy(proof["totals"]),
        "selection_scope": "filtered" if requested_libraries else "full",
        "requested_libraries": list(requested_libraries),
        "libraries": manifest_libraries,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument("--mode", default=DEFAULT_MODE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--library", action="append", dest="libraries", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        site_data = fetch_site_data(args.site_url)
        proof = find_proof(site_data, args.mode)
        manifest = build_selection_manifest(args.site_url, proof, args.libraries)
        output_path = write_json(args.output, manifest)
    except Exception as exc:  # pragma: no cover - CLI surface
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {output_path} with {len(manifest['libraries'])} fully passing libraries "
        f"for mode {manifest['selected_mode']}.",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
