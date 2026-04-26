# SafeLibs Docker Images

This repository builds and publishes Docker images for SafeLibs ports.

The current workflow starts from the live SafeLibs validator proof for
`port-04-test`. The tooling resolves the current fully passing library set,
writes a deterministic selection manifest to `dist/validator-selection.json`,
downloads and validates the published `.deb` artifacts for that selection, and
writes a lock manifest to `dist/port-debs-lock.json`. Materialized packages are
cached under `.work/debs/port-04-test/`.

Each library image is published as `safelibs/<library>`, where `<library>`
matches the SafeLibs port name without the `port-` prefix. The repository also
publishes an aggregate `safelibs/all` image that installs all available safe
library variants into a single base image for broad compatibility testing and
dependent application experiments.

Available commands:

- `make test` or `make check`: run the offline Python unit test suite.
- `make resolve-validator`: fetch the live validator payload and emit the
  current fully passing selection manifest. Set `LIBRARIES='lib1 lib2'` to
  request a filtered subset while preserving validator order.
- `make fetch-port-debs`: consume the selection manifest, materialize the
  expected release `.deb` files, verify checksum and package metadata, and emit
  the local lock manifest.

Docker build orchestration, image verification, CI publishing, and secret setup
are still added in later workflow phases.
