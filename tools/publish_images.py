"""Publish verified SafeLibs Docker images to Docker Hub."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import read_json

DEFAULT_IMAGE_BUILD_PLAN = Path("dist/image-build-plan.json")
DOCKER_COMMAND = "docker"
PUBLISH_IMAGE_NAMESPACE = "safelibs"


def load_image_build_plan(path: Path) -> dict:
    """Load the image build plan from disk."""

    plan = read_json(path)
    if not isinstance(plan, dict):
        raise ValueError("Image build plan must be a JSON object.")
    return plan


def require_full_selection(plan: dict) -> None:
    """Refuse to publish anything except the full validator selection."""

    selection_scope = plan.get("selection_scope")
    if selection_scope != "full":
        raise ValueError(
            "Refusing to publish image build plan with selection_scope "
            f"{selection_scope!r}; only 'full' plans are publishable."
        )

    requested_libraries = list(plan.get("requested_libraries") or [])
    if requested_libraries:
        raise ValueError(
            "Refusing to publish image build plan with requested_libraries "
            f"{requested_libraries!r}; subset plans are never publishable."
        )


def _docker_output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stderr.strip() or completed.stdout.strip() or "no output"


def assert_local_image_exists(image_ref: str) -> None:
    """Require that a publish candidate already exists locally."""

    completed = subprocess.run(
        [DOCKER_COMMAND, "image", "inspect", image_ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise FileNotFoundError(
            f"Local Docker image {image_ref!r} is not available for publish: "
            f"{_docker_output(completed)}"
        )


def push_image(image_ref: str) -> None:
    """Push a single Docker image reference."""

    completed = subprocess.run(
        [DOCKER_COMMAND, "push", image_ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Docker push failed for {image_ref}: {_docker_output(completed)}")


def _validate_publish_image_ref(image_ref: str) -> str:
    prefix = f"{PUBLISH_IMAGE_NAMESPACE}/"
    if not image_ref.startswith(prefix):
        raise ValueError(
            "Refusing to publish image_ref outside the required safelibs namespace: "
            f"{image_ref!r}"
        )

    repository, separator, tag = image_ref.partition(":")
    if separator != ":" or tag != "latest":
        raise ValueError(
            "Refusing to publish image_ref outside the required safelibs/*:latest contract: "
            f"{image_ref!r}"
        )

    leaf = repository[len(prefix) :]
    if not leaf or "/" in leaf:
        raise ValueError(
            "Refusing to publish malformed safelibs image_ref from build plan: "
            f"{image_ref!r}"
        )
    return leaf


def ordered_image_refs(plan: dict) -> list[str]:
    """Return deterministic publish order: per-library images, then aggregate."""

    if plan.get("image_namespace") != PUBLISH_IMAGE_NAMESPACE:
        raise ValueError(
            "Refusing to publish image build plan outside the required safelibs namespace: "
            f"{plan.get('image_namespace')!r}"
        )

    images = plan.get("images") or []
    if not images:
        raise ValueError("Image build plan does not contain any images to publish.")

    aggregate_image_ref = f"{PUBLISH_IMAGE_NAMESPACE}/all:latest"
    aggregate_libraries: list[str] | None = None
    per_library_refs_by_name: dict[str, str] = {}
    seen_refs: set[str] = set()
    for image_spec in images:
        image_ref = image_spec.get("image_ref")
        if not isinstance(image_ref, str) or not image_ref:
            raise ValueError("Image build plan contains an image without a valid image_ref.")
        image_name = _validate_publish_image_ref(image_ref)
        if image_ref in seen_refs:
            raise ValueError(f"Image build plan contains duplicate image_ref {image_ref!r}.")
        seen_refs.add(image_ref)

        libraries = list(image_spec.get("libraries") or [])
        if image_ref == aggregate_image_ref:
            if aggregate_libraries is not None:
                raise ValueError("Image build plan must contain exactly one aggregate all image.")
            if not libraries:
                raise ValueError("Aggregate image build plan is missing selection-order libraries.")
            if len(libraries) != len(set(libraries)):
                raise ValueError("Aggregate image build plan contains duplicate library names.")
            aggregate_libraries = libraries
            continue

        if libraries != [image_name]:
            raise ValueError(
                "Per-library image build plan libraries metadata must match its image_ref: "
                f"{image_ref!r}"
            )
        per_library_refs_by_name[image_name] = image_ref

    if aggregate_libraries is None:
        raise ValueError("Image build plan must contain exactly one aggregate all image.")

    aggregate_library_set = set(aggregate_libraries)
    missing_libraries = [
        library_name
        for library_name in aggregate_libraries
        if library_name not in per_library_refs_by_name
    ]
    extra_libraries = sorted(
        library_name
        for library_name in per_library_refs_by_name
        if library_name not in aggregate_library_set
    )
    if missing_libraries or extra_libraries:
        details: list[str] = []
        if missing_libraries:
            details.append("missing per-library images for " + ", ".join(missing_libraries))
        if extra_libraries:
            details.append("unexpected per-library images for " + ", ".join(extra_libraries))
        raise ValueError(
            "Image build plan does not match aggregate selection order: " + "; ".join(details)
        )

    return [per_library_refs_by_name[library_name] for library_name in aggregate_libraries] + [
        aggregate_image_ref
    ]


def publish_images(plan: dict) -> list[str]:
    """Validate and push the images described by the build plan."""

    require_full_selection(plan)
    image_refs = ordered_image_refs(plan)
    for image_ref in image_refs:
        assert_local_image_exists(image_ref)
    for image_ref in image_refs:
        push_image(image_ref)
    return image_refs


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-build-plan", type=Path, default=DEFAULT_IMAGE_BUILD_PLAN)
    parser.add_argument("--docker", default=DOCKER_COMMAND)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    global DOCKER_COMMAND
    DOCKER_COMMAND = args.docker

    try:
        plan = load_image_build_plan(args.image_build_plan)
        pushed_refs = publish_images(plan)
    except Exception as exc:  # pragma: no cover - CLI surface
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Pushed {len(pushed_refs)} Docker images.", file=sys.stdout)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
