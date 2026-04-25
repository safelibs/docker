# SafeLibs Docker Images

This repository builds and publishes Docker images for SafeLibs ports.

The intended artifact flow starts with tagged `github.com/safelibs/port-*`
repositories. For each eligible port release, the Docker pipeline downloads the
associated Debian packages from GitHub Releases, prepares a Docker build
context, installs the safe library package into the configured base image, and
publishes the resulting image to the `safelibs` Docker namespace.

Each library image is published as `safelibs/<library>`, where `<library>`
matches the SafeLibs port name without the `port-` prefix. The repository also
publishes an aggregate `safelibs/all` image that installs all available safe
library variants into a single base image for broad compatibility testing and
dependent application experiments.

This initial scaffold intentionally contains only repository metadata and
placeholders. Release discovery, Docker build orchestration, local verification,
CI publishing, and secret setup are added in later workflow phases.
