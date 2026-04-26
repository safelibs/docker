"""Verify SafeLibs Docker images against the deterministic build plan."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import read_json

DEFAULT_IMAGE_BUILD_PLAN = Path("dist/image-build-plan.json")
DOCKER_COMMAND = "docker"
PLAN_DIGEST_LABEL = "org.safelibs.image-plan-digest"


def load_image_build_plan(path: Path) -> dict:
    """Load the image build plan from disk."""

    plan = read_json(path)
    if not isinstance(plan, dict):
        raise ValueError("Image build plan must be a JSON object.")
    return plan


def query_installed_packages(image_ref: str, package_names: list[str]) -> dict[str, str]:
    """Return installed package versions for the requested package names."""

    if not package_names:
        return {}

    completed = subprocess.run(
        [
            DOCKER_COMMAND,
            "run",
            "--rm",
            image_ref,
            "dpkg-query",
            "-W",
            "-f=${Package}\t${Version}\n",
            *package_names,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and not (
        completed.returncode == 1 and _dpkg_query_reports_only_missing_packages(completed.stderr)
    ):
        output = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise RuntimeError(f"Docker package query failed for {image_ref}: {output}")

    observed: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        try:
            package_name, version = line.split("\t", 1)
        except ValueError as exc:
            raise ValueError(f"Unexpected dpkg-query output line for {image_ref}: {line!r}") from exc
        observed[package_name] = version
    return observed


def _dpkg_query_reports_only_missing_packages(stderr: str) -> bool:
    stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return bool(stderr_lines) and all("no packages found matching" in line for line in stderr_lines)


def _query_image_plan_digest(image_ref: str) -> str | None:
    format_string = "{{with .Config.Labels}}{{index . " + json.dumps(PLAN_DIGEST_LABEL) + "}}{{end}}"
    completed = subprocess.run(
        [
            DOCKER_COMMAND,
            "image",
            "inspect",
            "--format",
            format_string,
            image_ref,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        output = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise RuntimeError(f"Docker image inspect failed for {image_ref}: {output}")
    observed_digest = completed.stdout.strip()
    if observed_digest in {"", "<no value>", "<nil>"}:
        return None
    return observed_digest


def _is_aggregate_image(image_spec: dict) -> bool:
    return image_spec["image_ref"].rsplit("/", 1)[-1] == "all:latest"


def verify_image(image_spec: dict, *, require_plan_digest_match: bool = False) -> None:
    """Verify exact package versions for a single built image."""

    if require_plan_digest_match:
        expected_digest = image_spec.get("plan_digest")
        if not expected_digest:
            raise ValueError(f"Image plan for {image_spec['image_ref']} is missing plan_digest.")
        observed_digest = _query_image_plan_digest(image_spec["image_ref"])
        if observed_digest != expected_digest:
            raise ValueError(
                f"Image {image_spec['image_ref']} has plan digest {observed_digest!r}, "
                f"expected {expected_digest!r}"
            )

    expected_versions = {
        package["package"]: package["version"]
        for package in image_spec.get("packages") or []
    }
    observed_versions = query_installed_packages(
        image_spec["image_ref"],
        list(expected_versions),
    )

    missing_packages = [
        package_name
        for package_name in expected_versions
        if package_name not in observed_versions
    ]
    if missing_packages:
        raise ValueError(
            f"Image {image_spec['image_ref']} is missing expected packages: "
            + ", ".join(missing_packages)
        )

    mismatches = [
        (
            package_name,
            expected_versions[package_name],
            observed_versions[package_name],
        )
        for package_name in expected_versions
        if observed_versions.get(package_name) != expected_versions[package_name]
    ]
    if mismatches:
        mismatch_text = ", ".join(
            f"{package} expected {expected} observed {observed}"
            for package, expected, observed in mismatches
        )
        raise ValueError(f"Image {image_spec['image_ref']} failed package verification: {mismatch_text}")


def verify_build_plan(plan: dict, requested_libraries: list[str]) -> None:
    """Verify that the image plan matches the requested scope and built images."""

    expected_scope = "filtered" if requested_libraries else "full"
    if plan.get("selection_scope") != expected_scope:
        raise ValueError(
            f"Expected image build plan selection_scope {expected_scope!r}, "
            f"observed {plan.get('selection_scope')!r}"
        )

    if list(plan.get("requested_libraries") or []) != list(requested_libraries):
        raise ValueError(
            "Image build plan requested_libraries do not match the verification request."
        )

    images = plan.get("images") or []
    if not images:
        raise ValueError("Image build plan does not contain any images to verify.")

    if not any(image.get("image_ref", "").rsplit("/", 1)[-1] == "all:latest" for image in images):
        raise ValueError("Image build plan is missing the aggregate all image.")

    for image_spec in images:
        verify_image(
            image_spec,
            require_plan_digest_match=expected_scope == "filtered" and _is_aggregate_image(image_spec),
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-build-plan", type=Path, default=DEFAULT_IMAGE_BUILD_PLAN)
    parser.add_argument("--docker", default=DOCKER_COMMAND)
    parser.add_argument("--library", action="append", dest="libraries", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    global DOCKER_COMMAND
    DOCKER_COMMAND = args.docker

    try:
        plan = load_image_build_plan(args.image_build_plan)
        verify_build_plan(plan, args.libraries)
    except Exception as exc:  # pragma: no cover - CLI surface
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Verified {len(plan['images'])} Docker images.", file=sys.stdout)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
