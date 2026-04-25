# Phase Name
Local Docker Build And Runtime Verification

## Implement Phase ID
`implement-docker-build`

## Preexisting Inputs
- [Makefile](/home/yans/safelibs/docker/Makefile:1)
- `dist/validator-selection.json`
- `dist/port-debs-lock.json`
- `.work/debs/port-04-test/`
- The Phase 1 `resolve-validator` and `fetch-port-debs` Makefile targets
- The existing `BASE_IMAGE`, `WORKSPACE`, `DIST`, and `IMAGE_NAMESPACE` variables from [Makefile](/home/yans/safelibs/docker/Makefile:1)

## New Outputs
- `dist/image-build-plan.json`
- `.work/contexts/<library>/Dockerfile`
- `.work/contexts/<library>/debs/*.deb`
- `.work/contexts/all/Dockerfile`
- `.work/contexts/all/debs/*.deb`
- Local Docker images `safelibs/<library>:latest`
- Local Docker image `safelibs/all:latest`
- `tests/fixtures/port-debs-lock.json`
- `tests/fixtures/image-build-plan.json`

## File Changes
- Modify [Makefile](/home/yans/safelibs/docker/Makefile:1)
- Add `tools/build_images.py`
- Add `tools/verify_images.py`
- Add `tests/test_build_images.py`
- Add `tests/test_verify_images.py`
- Add `tests/fixtures/port-debs-lock.json`
- Add `tests/fixtures/image-build-plan.json`

## Implementation Details
- Consume the existing Phase 1 artifacts in place. This phase must build from `dist/validator-selection.json`, `dist/port-debs-lock.json`, and matching existing `.work/debs/port-04-test/` contents instead of refetching validator data or rediscovering release metadata.
- Keep Python code in `tools/` and tests in `tests/`.
- Keep dependencies in the Python standard library unless a new dependency is clearly justified.
- Invoke repository CLIs as modules, for example `python3 -m tools.build_images` and `python3 -m tools.verify_images`.
- Extend [Makefile](/home/yans/safelibs/docker/Makefile:1) so local commands and CI steps stay aligned. Preserve the existing targets from Phase 1 and add:
  - `IMAGE_BUILD_PLAN ?= $(DIST)/image-build-plan.json`
  - `CONTEXT_ROOT ?= $(WORKSPACE)/contexts`
  - `DOCKER ?= docker`
  - `build-images`
  - `verify-images`
- `make build-images` and `make verify-images` should pass `LIBRARIES` through to the Python tools so smoke runs can operate on a small subset without editing the lock files by hand. Both targets must allow `IMAGE_BUILD_PLAN` overrides, and `build-images` must also allow `CONTEXT_ROOT` overrides, so checker smoke runs can write scratch outputs under `.work/verify/`.
- Add `tools/build_images.py` with these functions:
  - `load_port_deb_lock(path: Path) -> dict`
  - `select_locked_libraries(lock_data: dict, requested_libraries: list[str]) -> list[dict]`
  - `build_image_plan(lock_data: dict, image_namespace: str, base_image: str, requested_libraries: list[str]) -> dict`
  - `render_dockerfile(base_image: str) -> str`
  - `prepare_context(image_spec: dict, workspace_root: Path) -> Path`
  - `docker_build(image_ref: str, context_dir: Path) -> None`
- `build_images.py` must consume `dist/port-debs-lock.json` only. It must not revisit the validator site or redownload `.deb` files when the lock and local artifacts already exist.
- Guardrail: compare `BASE_IMAGE` against the copied `suite.image` metadata inside `dist/port-debs-lock.json`. If the lock says the validator proof used `ubuntu:24.04` and the caller tries to build against another base image, fail clearly. The initial implementation should not provide a bypass for that mismatch.
- Build topology:
  - One image per selected library tagged as `$(IMAGE_NAMESPACE)/<library>:latest`
  - One aggregate image tagged as `$(IMAGE_NAMESPACE)/all:latest`
- When `LIBRARIES` is non-empty, Phase 2 may still build an aggregate `all` image for the requested subset as a local smoke-test convenience, but `dist/image-build-plan.json` or the alternate scratch plan must record `selection_scope: filtered` so Phase 3 can refuse to publish it.
- Docker contexts should be generated under `.work/contexts/`, not committed to git. Each context should contain only the locked `.deb` files for that image and a generated Dockerfile. Context generation must be deterministic: clear and recreate the target context directory before copying current inputs so stale files cannot leak between runs.
- If prepared artifacts such as downloaded `.deb` files under `.work/debs/port-04-test/` and generated Docker contexts under `.work/contexts/` already exist and still match expected metadata, later work in this phase should verify or reuse them in place instead of recreating them elsewhere unless the implementation is explicitly updating them.
- The Dockerfile should stay simple and reproducible:

```Dockerfile
FROM ${BASE_IMAGE}
COPY debs/ /tmp/debs/
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends /tmp/debs/*.deb && rm -rf /var/lib/apt/lists/* /tmp/debs
```

- `docker_build` should use `docker build --pull` so scheduled CI runs refresh the `ubuntu:24.04` base image before layering SafeLibs packages on top.
- The aggregate `all` image should be built from the union of every locked package in the selected scope. If two libraries ever provide the same package name with different version, filename, or SHA-256 data, `build_images.py` must fail rather than guessing which one should win. If duplicate package names are byte-for-byte identical, dedupe them.
- `dist/image-build-plan.json` should be the single source of truth for later verification and publish steps. It should include at least `selection_scope`, `requested_libraries`, `suite`, `base_image`, `image_namespace`, and an ordered `images` array. Each image entry should include `image_ref`, `libraries`, `packages`, and repo-relative `context_dir`.
- Add `tools/verify_images.py` with these functions:
  - `load_image_build_plan(path: Path) -> dict`
  - `query_installed_packages(image_ref: str, package_names: list[str]) -> dict[str, str]`
  - `verify_image(image_spec: dict) -> None`
  - `verify_build_plan(plan: dict, requested_libraries: list[str]) -> None`
- `verify_images.py` should use `docker run --rm <image> dpkg-query -W -f='${Package}\t${Version}\n' ...` and require exact version matches for every expected package. It should verify each per-library image plus the aggregate `all` image described by the selected build plan.
- Verification should cover only packages materialized from validator `port_debs`. Do not assert that `unported_original_packages` were replaced, because the validator schema explicitly allows fully passing libraries with unported original package names.
- Keep tests offline by mocking `subprocess.run` and filesystem operations. Cover:
  - Image-plan generation for per-library and aggregate images
  - Filtered-selection behavior
  - Duplicate-package conflict detection
  - Deterministic context generation and repo-relative `context_dir` values
  - Dockerfile rendering
  - Parsing of `dpkg-query` output and exact version matching

## Verification Phases
### `check-docker-build`
- Phase ID: `check-docker-build`
- Type: `check`
- `bounce_target`: `implement-docker-build`
- Purpose: Consume the Phase 1 lock artifacts, build per-library and aggregate images from the locked `.deb` files, and verify installed package versions inside each image without clobbering the canonical Phase 2 outputs.
- Commands:

```sh
python3 -m unittest discover -s tests -v
python3 - <<'PY'
import json
from pathlib import Path
plan = json.loads(Path('dist/image-build-plan.json').read_text())
if plan['selection_scope'] != 'full':
    raise SystemExit('expected canonical image-build plan to describe the full selection')
if not any(image['image_ref'] == 'safelibs/all:latest' for image in plan['images']):
    raise SystemExit('missing safelibs/all:latest in canonical image-build plan')
PY
make build-images LIBRARIES="cjson libpng" IMAGE_BUILD_PLAN=.work/verify/image-build-plan-smoke.json CONTEXT_ROOT=.work/verify/contexts
make verify-images LIBRARIES="cjson libpng" IMAGE_BUILD_PLAN=.work/verify/image-build-plan-smoke.json
```

## Success Criteria
- `dist/image-build-plan.json` is produced from `dist/port-debs-lock.json` without rediscovering validator or release metadata.
- The plan records repo-relative `context_dir` values, preserves `selection_scope`, and includes per-library images plus `safelibs/all:latest`.
- `.work/contexts/` contains deterministic Docker contexts built only from the locked `.deb` files for each image.
- The local build and verification path remains the repository's single-architecture `amd64` flow and does not depend on `docker buildx`, multi-architecture manifests, or non-`amd64` publish features.
- Local image verification checks exact package versions from `port_debs` and does not assert replacement of `unported_original_packages`.
- The unit tests, canonical plan sanity check, subset smoke build, and subset smoke verify commands pass without overwriting canonical `dist/*.json` artifacts or canonical `.work/contexts/` outputs.
- The canonical outputs also satisfy the final full-selection verification path: run `make build-images` and `make verify-images` for the complete current selection, and confirm `dist/image-build-plan.json` describes the full selection plus `safelibs/all:latest`.

## Git Commit Requirement
The generated implement prompt for this phase must instruct the implementer to commit all Phase 2 work to git before yielding.
