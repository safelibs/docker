"""Build SafeLibs Docker images from the locked Debian artifact set."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import read_json, repo_relative_path, resolve_repo_path, sha256_file, write_json

DEFAULT_LOCK_MANIFEST = Path("dist/port-debs-lock.json")
DEFAULT_OUTPUT = Path("dist/image-build-plan.json")
DEFAULT_CONTEXT_ROOT = Path(".work/contexts")
DEFAULT_IMAGE_NAMESPACE = "safelibs"
DEFAULT_BASE_IMAGE = "ubuntu:24.04"
DOCKER_COMMAND = "docker"
PLAN_DIGEST_LABEL = "org.safelibs.image-plan-digest"
BASE_IMAGE_ID_LABEL = "org.safelibs.base-image-id"
_IMAGE_PLAN_DIGESTS_BY_REF: dict[str, str] = {}
_CURRENT_BASE_IMAGE_ID = ""
FULL_AGGREGATE_CACHE_TAG = "full-selection-cache"


def load_port_deb_lock(path: Path) -> dict:
    """Load the locked SafeLibs Debian manifest from disk."""

    lock_data = read_json(path)
    if not isinstance(lock_data, dict):
        raise ValueError("Port deb lock manifest must be a JSON object.")
    return lock_data


def select_locked_libraries(lock_data: dict, requested_libraries: list[str]) -> list[dict]:
    """Select locked libraries, preserving manifest order."""

    libraries = [copy.deepcopy(entry) for entry in lock_data.get("libraries") or []]
    if not requested_libraries:
        return libraries

    libraries_by_name = {entry.get("library"): entry for entry in libraries}
    missing = [library for library in requested_libraries if library not in libraries_by_name]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(
            "Requested libraries are not present in the locked selection: "
            f"{missing_list}"
        )

    requested_names = set(requested_libraries)
    return [entry for entry in libraries if entry.get("library") in requested_names]


def _context_name_for_image(image_ref: str) -> str:
    leaf = image_ref.rsplit("/", 1)[-1]
    return leaf.split(":", 1)[0]


def _context_dir_for_image(context_name: str, context_root: Path) -> str:
    return repo_relative_path(resolve_repo_path(context_root) / context_name)


def _validate_base_image(lock_data: dict, base_image: str) -> None:
    suite = lock_data.get("suite") or {}
    locked_base_image = suite.get("image")
    if locked_base_image != base_image:
        raise ValueError(
            "BASE_IMAGE does not match the locked validator suite image: "
            f"lock uses {locked_base_image!r}, requested {base_image!r}"
        )


def _package_signature(package: dict) -> tuple[str | None, str | None, str | None, str | None, int | None]:
    return (
        package.get("version"),
        package.get("filename"),
        package.get("sha256"),
        package.get("architecture"),
        package.get("size"),
    )


def _build_image_spec(
    image_ref: str,
    libraries: list[str],
    packages: list[dict],
    base_image: str,
) -> dict:
    context_name = _context_name_for_image(image_ref)
    image_spec = {
        "image_ref": image_ref,
        "base_image": base_image,
        "libraries": list(libraries),
        "packages": [copy.deepcopy(package) for package in packages],
        "context_dir": _context_dir_for_image(context_name, DEFAULT_CONTEXT_ROOT),
    }
    image_spec["plan_digest"] = _image_plan_digest(image_spec)
    return image_spec


def _build_aggregate_packages(selected_libraries: list[dict]) -> list[dict]:
    aggregate_packages: list[dict] = []
    packages_by_name: dict[str, dict] = {}

    for library_entry in selected_libraries:
        library_name = library_entry["library"]
        for package in library_entry.get("port_debs") or []:
            package_name = package["package"]
            existing = packages_by_name.get(package_name)
            if existing is None:
                package_copy = copy.deepcopy(package)
                packages_by_name[package_name] = package_copy
                aggregate_packages.append(package_copy)
                continue

            if _package_signature(existing) != _package_signature(package):
                raise ValueError(
                    "Aggregate image package conflict for "
                    f"{package_name!r}: {existing['filename']} conflicts with "
                    f"{package['filename']} from library {library_name!r}"
                )

    return aggregate_packages


def _image_plan_digest(image_spec: dict) -> str:
    payload = {
        "base_image": image_spec["base_image"],
        "libraries": list(image_spec.get("libraries") or []),
        "packages": [
            {
                "package": package["package"],
                "version": package["version"],
                "architecture": package["architecture"],
                "filename": package["filename"],
                "sha256": package["sha256"],
                "size": package["size"],
            }
            for package in image_spec.get("packages") or []
        ],
    }
    encoded_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded_payload).hexdigest()


def build_image_plan(
    lock_data: dict,
    image_namespace: str,
    base_image: str,
    requested_libraries: list[str],
) -> dict:
    """Build the deterministic image plan from the locked Debian manifest."""

    _validate_base_image(lock_data, base_image)
    selected_libraries = select_locked_libraries(lock_data, requested_libraries)
    if not selected_libraries:
        raise ValueError("No locked libraries were selected for image building.")

    effective_requested_libraries = (
        list(requested_libraries)
        if requested_libraries
        else list(lock_data.get("requested_libraries") or [])
    )
    selection_scope = "filtered" if requested_libraries else lock_data.get("selection_scope", "full")

    images = []
    selected_library_names = [entry["library"] for entry in selected_libraries]
    for library_entry in selected_libraries:
        image_ref = f"{image_namespace}/{library_entry['library']}:latest"
        images.append(
            _build_image_spec(
                image_ref=image_ref,
                libraries=[library_entry["library"]],
                packages=library_entry.get("port_debs") or [],
                base_image=base_image,
            )
        )

    aggregate_packages = _build_aggregate_packages(selected_libraries)
    aggregate_image_ref = f"{image_namespace}/all:latest"
    images.append(
        _build_image_spec(
            image_ref=aggregate_image_ref,
            libraries=selected_library_names,
            packages=aggregate_packages,
            base_image=base_image,
        )
    )

    return {
        "site_url": lock_data["site_url"],
        "schema_version": lock_data["schema_version"],
        "proof_version": lock_data["proof_version"],
        "selected_mode": lock_data["selected_mode"],
        "suite": copy.deepcopy(lock_data["suite"]),
        "proof_totals": copy.deepcopy(lock_data["proof_totals"]),
        "selection_scope": selection_scope,
        "requested_libraries": effective_requested_libraries,
        "base_image": base_image,
        "image_namespace": image_namespace,
        "images": images,
    }


def render_dockerfile(base_image: str) -> str:
    """Render the deterministic Dockerfile used for all SafeLibs images."""

    return (
        "# syntax=docker/dockerfile:1\n"
        f"FROM {base_image}\n"
        "COPY debs/ /tmp/debs/\n"
        "RUN --mount=type=cache,target=/var/cache/apt,sharing=locked "
        "--mount=type=cache,target=/var/lib/apt,sharing=locked "
        "set -eux; rm -f /etc/apt/apt.conf.d/docker-clean; "
        "for attempt in 1 2 3; do apt-get update -o Acquire::Retries=3 && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -o Acquire::Retries=3 "
        "-o DPkg::Use-Pty=0 --no-install-recommends /tmp/debs/*.deb && break; "
        "if [ \"$attempt\" -eq 3 ]; then exit 1; fi; rm -rf /var/lib/apt/lists/*; "
        "sleep \"$attempt\"; done; rm -rf /tmp/debs\n"
    )


def _validate_local_deb(package: dict) -> Path:
    local_path = package.get("local_path")
    if not local_path:
        raise ValueError(f"Locked package {package.get('package')!r} is missing local_path metadata.")

    deb_path = resolve_repo_path(local_path)
    if not deb_path.exists():
        raise FileNotFoundError(
            f"Locked package artifact is missing from disk: {repo_relative_path(deb_path)}"
        )

    expected_size = package.get("size")
    if expected_size is not None and deb_path.stat().st_size != expected_size:
        raise ValueError(
            f"Size mismatch for {repo_relative_path(deb_path)}: "
            f"expected {expected_size}, observed {deb_path.stat().st_size}"
        )

    actual_sha = sha256_file(deb_path)
    if actual_sha != package["sha256"]:
        raise ValueError(
            f"SHA-256 mismatch for {repo_relative_path(deb_path)}: "
            f"expected {package['sha256']}, observed {actual_sha}"
        )

    return deb_path


def prepare_context(image_spec: dict, workspace_root: Path) -> Path:
    """Create a deterministic Docker build context for a single image."""

    context_root = resolve_repo_path(workspace_root)
    context_name = _context_name_for_image(image_spec["image_ref"])
    context_dir = context_root / context_name
    if context_dir.exists():
        if context_dir.is_dir():
            shutil.rmtree(context_dir)
        else:
            context_dir.unlink()

    debs_dir = context_dir / "debs"
    debs_dir.mkdir(parents=True, exist_ok=True)
    for package in image_spec.get("packages") or []:
        source_path = _validate_local_deb(package)
        shutil.copyfile(source_path, debs_dir / package["filename"])

    (context_dir / "Dockerfile").write_text(
        render_dockerfile(image_spec["base_image"]),
        encoding="utf-8",
    )

    image_spec["context_dir"] = repo_relative_path(context_dir)
    return context_dir


def _docker_run(
    command: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=command_env,
    )
    if check and completed.returncode != 0:
        output = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise RuntimeError(f"Docker command failed: {' '.join(command)}: {output}")
    return completed


def _refresh_base_image(base_image: str) -> str:
    _docker_run([DOCKER_COMMAND, "pull", base_image])
    completed = _docker_run(
        [DOCKER_COMMAND, "image", "inspect", "--format", "{{.Id}}", base_image]
    )
    base_image_id = completed.stdout.strip()
    if not base_image_id:
        raise ValueError(f"Docker image inspect did not return an image id for {base_image}.")
    return base_image_id


def _dpkg_query_reports_only_missing_packages(stderr: str) -> bool:
    stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return bool(stderr_lines) and all("no packages found matching" in line for line in stderr_lines)


def _local_image_exists(image_ref: str) -> bool:
    completed = _docker_run([DOCKER_COMMAND, "image", "inspect", image_ref], check=False)
    return completed.returncode == 0


def _query_installed_packages(image_ref: str, package_names: list[str]) -> dict[str, str] | None:
    if not package_names:
        return {}

    completed = _docker_run(
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
    )
    if completed.returncode != 0 and not (
        completed.returncode == 1
        and _dpkg_query_reports_only_missing_packages(completed.stderr)
    ):
        return None

    observed: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        try:
            package_name, version = line.split("\t", 1)
        except ValueError:
            return None
        observed[package_name] = version
    return observed


def _query_image_label(image_ref: str, label_name: str) -> str | None:
    format_string = "{{with .Config.Labels}}{{index . " + json.dumps(label_name) + "}}{{end}}"
    completed = _docker_run(
        [
            DOCKER_COMMAND,
            "image",
            "inspect",
            "--format",
            format_string,
            image_ref,
        ],
        check=False,
    )
    if completed.returncode != 0:
        return None
    observed_digest = completed.stdout.strip()
    if observed_digest in {"", "<no value>", "<nil>"}:
        return None
    return observed_digest


def _query_image_plan_digest(image_ref: str) -> str | None:
    return _query_image_label(image_ref, PLAN_DIGEST_LABEL)


def _query_image_base_id(image_ref: str) -> str | None:
    return _query_image_label(image_ref, BASE_IMAGE_ID_LABEL)


def _is_aggregate_image(image_spec: dict) -> bool:
    return image_spec["image_ref"].rsplit("/", 1)[-1] == "all:latest"


def _aggregate_cache_image_ref(image_ref: str) -> str:
    namespace = image_ref.rsplit("/", 1)[0]
    return f"{namespace}/all:{FULL_AGGREGATE_CACHE_TAG}"


def _aggregate_cache_image_spec(image_spec: dict) -> dict:
    cache_image_spec = copy.deepcopy(image_spec)
    cache_image_spec["image_ref"] = _aggregate_cache_image_ref(image_spec["image_ref"])
    return cache_image_spec


def _build_execution_order(
    prepared_contexts: list[tuple[dict, Path]],
) -> list[tuple[dict, Path]]:
    aggregate_contexts = [item for item in prepared_contexts if _is_aggregate_image(item[0])]
    library_contexts = [item for item in prepared_contexts if not _is_aggregate_image(item[0])]
    return aggregate_contexts + library_contexts


def _local_image_matches(image_spec: dict) -> bool:
    image_ref = image_spec["image_ref"]
    if not _local_image_exists(image_ref):
        return False
    if _query_image_plan_digest(image_ref) != image_spec.get("plan_digest"):
        return False
    if _query_image_base_id(image_ref) != _CURRENT_BASE_IMAGE_ID:
        return False

    expected_versions = {
        package["package"]: package["version"]
        for package in image_spec.get("packages") or []
    }
    observed_versions = _query_installed_packages(image_ref, list(expected_versions))
    if observed_versions is None:
        return False
    return observed_versions == expected_versions


def _docker_tag(source_ref: str, target_ref: str) -> None:
    _docker_run([DOCKER_COMMAND, "tag", source_ref, target_ref])


def _preserve_full_aggregate_cache(image_spec: dict) -> None:
    cache_image_spec = _aggregate_cache_image_spec(image_spec)
    if _local_image_matches(cache_image_spec):
        return
    _docker_tag(image_spec["image_ref"], cache_image_spec["image_ref"])


def _restore_full_aggregate_from_cache(image_spec: dict) -> bool:
    cache_image_spec = _aggregate_cache_image_spec(image_spec)
    if not _local_image_matches(cache_image_spec):
        return False
    _docker_tag(cache_image_spec["image_ref"], image_spec["image_ref"])
    return True


def docker_build(image_ref: str, context_dir: Path) -> None:
    """Build a single Docker image from a prepared context."""

    plan_digest = _IMAGE_PLAN_DIGESTS_BY_REF.get(image_ref)
    if not plan_digest:
        raise ValueError(f"Missing plan digest for Docker image {image_ref}.")
    if not _CURRENT_BASE_IMAGE_ID:
        raise ValueError(f"Missing base image id for Docker image {image_ref}.")
    _docker_run(
        [
            DOCKER_COMMAND,
            "build",
            "--pull",
            "--label",
            f"{PLAN_DIGEST_LABEL}={plan_digest}",
            "--label",
            f"{BASE_IMAGE_ID_LABEL}={_CURRENT_BASE_IMAGE_ID}",
            "-t",
            image_ref,
            str(context_dir),
        ],
        env={"DOCKER_BUILDKIT": "1"},
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-manifest", type=Path, default=DEFAULT_LOCK_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--context-root", type=Path, default=DEFAULT_CONTEXT_ROOT)
    parser.add_argument("--image-namespace", default=DEFAULT_IMAGE_NAMESPACE)
    parser.add_argument("--base-image", default=DEFAULT_BASE_IMAGE)
    parser.add_argument("--docker", default=DOCKER_COMMAND)
    parser.add_argument("--library", action="append", dest="libraries", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    global DOCKER_COMMAND, _CURRENT_BASE_IMAGE_ID
    DOCKER_COMMAND = args.docker

    try:
        _IMAGE_PLAN_DIGESTS_BY_REF.clear()
        _CURRENT_BASE_IMAGE_ID = _refresh_base_image(args.base_image)
        lock_data = load_port_deb_lock(args.lock_manifest)
        plan = build_image_plan(
            lock_data=lock_data,
            image_namespace=args.image_namespace,
            base_image=args.base_image,
            requested_libraries=args.libraries,
        )
        plan["base_image_id"] = _CURRENT_BASE_IMAGE_ID
        full_aggregate_spec = build_image_plan(
            lock_data=lock_data,
            image_namespace=args.image_namespace,
            base_image=args.base_image,
            requested_libraries=[],
        )["images"][-1]
        if plan["selection_scope"] == "filtered" and _local_image_matches(full_aggregate_spec):
            _preserve_full_aggregate_cache(full_aggregate_spec)
        elif plan["selection_scope"] == "full" and not _local_image_matches(full_aggregate_spec):
            _restore_full_aggregate_from_cache(full_aggregate_spec)
        prepared_contexts: list[tuple[dict, Path]] = []
        for image_spec in plan["images"]:
            _IMAGE_PLAN_DIGESTS_BY_REF[image_spec["image_ref"]] = image_spec["plan_digest"]
            context_dir = prepare_context(image_spec, args.context_root)
            prepared_contexts.append((image_spec, context_dir))
        output_path = write_json(args.output, plan)
        built_images = 0
        for image_spec, context_dir in _build_execution_order(prepared_contexts):
            if _local_image_matches(image_spec):
                continue
            docker_build(image_spec["image_ref"], context_dir)
            built_images += 1
        if plan["selection_scope"] == "full" and _local_image_matches(full_aggregate_spec):
            _preserve_full_aggregate_cache(full_aggregate_spec)
    except Exception as exc:  # pragma: no cover - CLI surface
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {output_path} and built {built_images} Docker images.",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
