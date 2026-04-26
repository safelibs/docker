# SafeLibs Docker Images

This repository builds and publishes Docker images for the SafeLibs libraries
that currently pass the validator proof for `mode == "port-04-test"`. The
selection rule is validator-driven and strict: only libraries whose current
proof entry has zero failed cases and whose passed count matches the total case
count are eligible. The resulting selection is written to
`dist/validator-selection.json`.

The published `.deb` artifacts for that selection are then locked in
`dist/port-debs-lock.json` and materialized under `.work/debs/port-04-test/`.
`unported_original_packages` are preserved as metadata from the validator proof
so operators can see which original Ubuntu packages were not ported, but they
are not synthesized into separate SafeLibs `.deb` artifacts.

Docker images are built from the locked `.deb` set into one image per library
plus the aggregate `safelibs/all:latest` image. The default base image is
`ubuntu:24.04` because the validator suite and the prepared SafeLibs `.deb`
artifacts are locked against the Ubuntu Noble environment encoded in the proof
metadata; changing the base image would break that contract.

## Local Workflow

Run the commands in this order when you want a fresh local build:

- `make resolve-validator`
  Fetch the live validator payload and write `dist/validator-selection.json`.
  Set `LIBRARIES='cjson libpng'` to request a subset while preserving validator
  order.
- `make fetch-port-debs`
  Consume the selection manifest, reuse any matching files already present under
  `.work/debs/port-04-test/`, verify package metadata and digests, and write
  `dist/port-debs-lock.json`.
- `make build-images`
  Build per-library images plus `safelibs/all:latest` from the locked package
  set, reusing matching local contexts under `.work/contexts/`, and write
  `dist/image-build-plan.json`.
- `make verify-images`
  Run `dpkg-query` inside the built images and verify labels, package versions,
  and the base image id against `dist/image-build-plan.json`.
- `make publish-images`
  Push the images described by `dist/image-build-plan.json`. This command
  refuses to publish anything except a full selection plan with an empty
  `requested_libraries` list, and it checks that every image exists locally
  before any push begins.

`make test` runs the unit tests, and `make check` remains the lightweight alias
for the same offline suite.

## Publish Policy

Images are published to Docker Hub as `safelibs/<library>:latest` for each
selected library and `safelibs/all:latest` for the aggregate image. The initial
publish path is `amd64` only. This repository does not use `docker buildx`,
multi-architecture manifests, alternate registries, or alternate repository
names in the initial workflow.

Subset runs are allowed only for local or manual dry-run smoke tests. A subset
selection such as `LIBRARIES='cjson libpng'` is never publishable, even if the
subset builds and verifies successfully.

## GitHub Actions

The CI workflow runs a single linear job on `ubuntu-24.04`:

1. `make test`
2. `make resolve-validator`
3. `make fetch-port-debs`
4. `make build-images` on scheduled or manual runs
5. `make verify-images` on scheduled or manual runs
6. `make publish-images` when publishing is allowed for the event

Pushes to `main` run the lightweight validation path (`test`, validator
selection, and `.deb` locking) so commit feedback stays fast. The full Docker
build, verify, and publish path runs on the daily scheduled workflow and on
`workflow_dispatch`. `workflow_dispatch` defaults to a no-push dry run. Manual
publishes are allowed only from `main` and only when the libraries input is
empty.

The required repository secrets are exactly:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

These secrets must identify a Docker Hub user or service account with write
access to `safelibs/<library>:latest` and `safelibs/all:latest`.
