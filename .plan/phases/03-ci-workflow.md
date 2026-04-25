# Phase Name
CI Workflow, Publish Orchestration, And Documentation

## Implement Phase ID
`implement-ci-workflow`

## Preexisting Inputs
- [README.md](/home/yans/safelibs/docker/README.md:1)
- [Makefile](/home/yans/safelibs/docker/Makefile:1)
- [tools/__init__.py](/home/yans/safelibs/docker/tools/__init__.py:1)
- `tools/validator_selection.py`
- `tools/fetch_port_debs.py`
- `tools/build_images.py`
- `tools/verify_images.py`
- `tests/test_validator_selection.py`
- `tests/test_fetch_port_debs.py`
- `tests/test_build_images.py`
- `tests/test_verify_images.py`
- `tests/fixtures/validator-site-data.json`
- `tests/fixtures/validator-selection.json`
- `tests/fixtures/port-debs-lock.json`
- `tests/fixtures/image-build-plan.json`
- `dist/validator-selection.json`
- `dist/port-debs-lock.json`
- `dist/image-build-plan.json`
- `.work/debs/port-04-test/`
- `.work/contexts/`
- `DOCKERHUB_USERNAME` for a Docker Hub user or service account with push access to the `safelibs` namespace
- `DOCKERHUB_TOKEN` for that account with write access to `safelibs/*`
- The existing `safelibs` Docker Hub namespace with authorization to push `safelibs/<library>:latest` and `safelibs/all:latest`

## New Outputs
- `.github/workflows/ci.yml`
- Updated [README.md](/home/yans/safelibs/docker/README.md:1)
- Updated [Makefile](/home/yans/safelibs/docker/Makefile:1)
- `tools/publish_images.py`
- `tests/test_publish_images.py`

## File Changes
- Add `.github/workflows/ci.yml`
- Modify [README.md](/home/yans/safelibs/docker/README.md:1)
- Modify [Makefile](/home/yans/safelibs/docker/Makefile:1)
- Add `tools/publish_images.py`
- Add `tests/test_publish_images.py`

## Implementation Details
- Consume Phase 1 and Phase 2 artifacts in place instead of regenerating them. This phase must reuse `dist/validator-selection.json`, `dist/port-debs-lock.json`, `dist/image-build-plan.json`, matching `.work/debs/port-04-test/` contents, and any matching `.work/contexts/` outputs rather than rederiving selection, release, or image-topology data from scratch.
- Keep the Python tooling and tests standard-library-only unless a new dependency is clearly justified.
- Invoke repository CLIs as modules everywhere they are called or documented, for example `python3 -m tools.publish_images`, not `python3 tools/publish_images.py`.
- Keep the initial workflow and publish path `amd64` only. Do not introduce `docker buildx`, multi-architecture manifests, or any non-`amd64` publish targets.
- Use the built-in Actions `GITHUB_TOKEN` only for checkout and other first-party actions. Do not add a custom GitHub release token; release downloads stay on the public URLs encoded in the validator-derived artifacts.
- Publish only to `safelibs/*` in the initial implementation. Do not add alternate registries or alternate repository names.
- Add `tools/publish_images.py` so publish behavior is deterministic and driven by `dist/image-build-plan.json` instead of shell loops scattered across CI. It should expose:
  - `load_image_build_plan(path: Path) -> dict`
  - `require_full_selection(plan: dict) -> None`
  - `assert_local_image_exists(image_ref: str) -> None`
  - `push_image(image_ref: str) -> None`
- `publish_images.py` must:
  - Refuse to publish when `selection_scope != "full"` or `requested_libraries` is non-empty.
  - Push images in deterministic order: per-library images in selection order, then `safelibs/all:latest`.
  - Check that each image exists locally before attempting `docker push`, so CI fails with a targeted error instead of a later registry error.
- Extend [Makefile](/home/yans/safelibs/docker/Makefile:1) with `publish-images` and wire it to `tools/publish_images.py`. Keep `check` as the lightweight unit-test target. CI should call the heavier resolve, fetch, build, and verify targets explicitly before publish.
- Create `.github/workflows/ci.yml` with a single linear job on `ubuntu-24.04`. Add workflow-level `concurrency` with `cancel-in-progress: false` so overlapping scheduled, push, and manual runs cannot race each other while updating `:latest` tags.
- Required triggers:
  - `push` on `main`
  - Daily `schedule` using a fixed cron expression
  - `workflow_dispatch`
- `workflow_dispatch` inputs should be:
  - `push_images`: boolean, default `false`
  - `libraries`: optional string, default empty, passed through to `LIBRARIES`
- The workflow step order must be explicit and linear:
  - Checkout
  - Set up Python
  - Compute `should_push` and normalize the optional `libraries` input
  - Validate the manual publish contract
  - Run `make test`
  - Run `make resolve-validator`
  - Run `make fetch-port-debs`
  - Run `make build-images`
  - Run `make verify-images`
  - Validate Docker Hub secrets when `should_push=true`
  - Log in to Docker Hub when `should_push=true`
  - Run `make publish-images` when `should_push=true`
  - Upload `dist/` as an artifact with `if: always()`
- `should_push` must be computed concretely:
  - `true` on `push` to `main`
  - `true` on `schedule`
  - `true` on `workflow_dispatch` only when the boolean input `push_images` is `true`
  - `false` otherwise
- The manual publish validation step must:
  - Fail if `should_push=true` and the ref is not `refs/heads/main`
  - Fail if `should_push=true` and the normalized `libraries` value is non-empty
  - Permit subset dry runs when `should_push=false`
- Secret handling must be concrete:
  - Use `secrets.DOCKERHUB_USERNAME` and `secrets.DOCKERHUB_TOKEN` exactly.
  - Do not introduce `DOCKER_USERNAME`, `DOCKER_TOKEN`, `DOCKERHUB_PASSWORD`, `GH_TOKEN`, or any other alternative names in the initial implementation.
  - Add a shell validation step before login that exits nonzero with a clear message when the run is expected to push and either Docker Hub secret is empty.
  - Use an inline `docker login --password-stdin` step so the login behavior is explicit in the workflow file.
- Push policy must be explicit:
  - Scheduled runs and pushes to `main` should publish automatically after verification succeeds.
  - `workflow_dispatch` should default to a no-push dry run.
  - If any test, fetch, build, or verification step fails, nothing should be pushed.
- No custom GitHub release secret belongs in the initial workflow. The `.deb` download step should rely on the public release URLs encoded in `dist/validator-selection.json`.
- Update [README.md](/home/yans/safelibs/docker/README.md:1) so operators can use the repository without reading the implementation:
  - Explain that proof selection keys off `mode == "port-04-test"`
  - Explain the validator-driven fully-passing selection rule
  - Explain why the default base image is `ubuntu:24.04`
  - Document the local commands `make resolve-validator`, `make fetch-port-debs`, `make build-images`, `make verify-images`, and `make publish-images`
  - Document the exact required GitHub Actions secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
  - Document that manual subset runs are for dry-run smoke tests only and are never publishable
  - Document that the initial publish path is `amd64` only
  - Document that `unported_original_packages` are preserved as metadata and are not separately synthesized into SafeLibs `.deb` artifacts

## Verification Phases
### `check-ci-workflow`
- Phase ID: `check-ci-workflow`
- Type: `check`
- `bounce_target`: `implement-ci-workflow`
- Purpose: Confirm that the GitHub Actions workflow uses the local toolchain in the correct order, enforces the exact Docker Hub secret contract, refuses partial or non-main manual publish runs, and documents the final operator flow.
- Commands:

```sh
python3 -m unittest discover -s tests -v
make check
python3 - <<'PY'
from pathlib import Path
workflow = Path('.github/workflows/ci.yml').read_text()
required = [
    'runs-on: ubuntu-24.04',
    'schedule:',
    'workflow_dispatch:',
    'push_images',
    'libraries',
    'DOCKERHUB_USERNAME',
    'DOCKERHUB_TOKEN',
    'make test',
    'make resolve-validator',
    'make fetch-port-debs',
    'make build-images',
    'make verify-images',
    'make publish-images',
    'if: always()',
]
missing = [item for item in required if item not in workflow]
if missing:
    raise SystemExit('missing workflow elements: ' + ', '.join(missing))
PY
```

- Review checks:
  - `Confirm the workflow computes an explicit should_push decision before any publish-only step`
  - `Confirm the workflow refuses workflow_dispatch publish runs from refs other than refs/heads/main`
  - `Confirm the workflow refuses workflow_dispatch runs that set both push_images=true and a non-empty libraries filter`
  - `Confirm the workflow fails before login when should_push=true and either DOCKERHUB_USERNAME or DOCKERHUB_TOKEN is missing`
  - `Confirm the login and publish steps are gated on should_push == true`
  - `Confirm the artifact upload step uses if: always() so dist/ survives failures`

## Success Criteria
- `.github/workflows/ci.yml` is a single linear `ubuntu-24.04` job that runs the local toolchain in the required order and uploads `dist/` with `if: always()`.
- The workflow computes one explicit `should_push` value, blocks subset publish attempts, blocks manual non-main publish attempts, and fails before login when required Docker Hub secrets are missing.
- The only custom publish secrets referenced are `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.
- The workflow, [Makefile](/home/yans/safelibs/docker/Makefile:1), and [README.md](/home/yans/safelibs/docker/README.md:1) keep module-style CLI invocation, remain standard-library-oriented, use the built-in `GITHUB_TOKEN` only for checkout and first-party actions, and keep the initial publish scope `amd64` only without `docker buildx`.
- `tools/publish_images.py` publishes only full-selection plans in deterministic order after confirming local images exist.
- `tests/test_publish_images.py` covers filtered-plan refusal, local-image existence checks, and deterministic push ordering.
- The final hosted verification contract remains intact:
  - Trigger `workflow_dispatch` with `push_images=false` and `libraries="cjson libpng"` to confirm the dry-run subset path works without any publish attempt.
  - Trigger `workflow_dispatch` with `push_images=true` and `libraries="cjson"` to confirm the workflow fails before login because subset publish is forbidden.
  - Trigger `workflow_dispatch` with `push_images=true` and an empty `libraries` value from `main`, or observe the next `main` or scheduled publish run, then pull at least one per-library image plus `safelibs/all:latest` and verify package versions inside those containers with `dpkg-query`.

## Git Commit Requirement
The generated implement prompt for this phase must instruct the implementer to commit all Phase 3 work to git before yielding.
