# Phase Name
Validator Selection And Port Artifact Locking

## Implement Phase ID
`implement-validator-selection`

## Preexisting Inputs
- [README.md](/home/yans/safelibs/docker/README.md:1)
- [Makefile](/home/yans/safelibs/docker/Makefile:1)
- [.gitignore](/home/yans/safelibs/docker/.gitignore:1)
- [tools/__init__.py](/home/yans/safelibs/docker/tools/__init__.py:1)
- [tests/__init__.py](/home/yans/safelibs/docker/tests/__init__.py:1)
- The existing `.work/` and `dist/` directory contract
- The live validator payload `https://safelibs.github.io/validator/site-data.json`
- The existing `port-04-test` proof fields `mode`, `proof_version`, `suite`, `totals`, `libraries`, `port_repository`, `port_release_tag`, `port_commit`, and `port_debs`

## New Outputs
- `dist/validator-selection.json`
- `dist/port-debs-lock.json`
- `.work/debs/port-04-test/<library>/<filename>.deb`
- `tests/fixtures/validator-site-data.json`
- `tests/fixtures/validator-selection.json`

## File Changes
- Modify [Makefile](/home/yans/safelibs/docker/Makefile:1)
- Modify [tools/__init__.py](/home/yans/safelibs/docker/tools/__init__.py:1)
- Add `tools/validator_selection.py`
- Add `tools/fetch_port_debs.py`
- Add `tests/test_validator_selection.py`
- Add `tests/test_fetch_port_debs.py`
- Add `tests/fixtures/validator-site-data.json`
- Add `tests/fixtures/validator-selection.json`

## Implementation Details
- Consume the existing repository artifacts in place rather than replacing them. Reuse [README.md](/home/yans/safelibs/docker/README.md:1), [Makefile](/home/yans/safelibs/docker/Makefile:1), [.gitignore](/home/yans/safelibs/docker/.gitignore:1), [tools/__init__.py](/home/yans/safelibs/docker/tools/__init__.py:1), and the existing `.work/` and `dist/` directory conventions.
- Keep Python code in `tools/` and tests in `tests/`.
- Keep the test runner as `python3 -m unittest discover -s tests -v`.
- Keep dependencies in the Python standard library unless a new dependency is clearly justified.
- Invoke repository CLIs as modules, for example `python3 -m tools.validator_selection`, not `python3 tools/validator_selection.py`, so shared imports from `tools` work consistently.
- Assume Linux for non-unit-test execution. `fetch-port-debs` requires `dpkg-deb`.
- Expand [tools/__init__.py](/home/yans/safelibs/docker/tools/__init__.py:1) into a shared helper module with small standard-library utilities used by all later CLIs: stable JSON reading and writing, repo-relative path handling, directory creation, SHA-256 hashing, and a `run_checked(argv: list[str]) -> str` subprocess helper for `dpkg-deb`, `docker`, and other shell calls.
- Extend [Makefile](/home/yans/safelibs/docker/Makefile:1) while preserving the current `test`, `check`, and `clean` targets, and keep `clean` removing `.work/` and `dist/`. Phase 1 must add:
  - `VALIDATOR_SITE_URL ?= https://safelibs.github.io/validator/site-data.json`
  - `PORT_MODE ?= port-04-test`
  - `SELECTION_MANIFEST ?= $(DIST)/validator-selection.json`
  - `PORT_DEB_ROOT ?= $(WORKSPACE)/debs/$(PORT_MODE)`
  - `PORT_DEB_LOCK ?= $(DIST)/port-debs-lock.json`
  - `LIBRARIES ?=`
  - `VERIFY_ROOT ?= $(WORKSPACE)/verify`
  - `resolve-validator`
  - `fetch-port-debs`
- `make resolve-validator` should translate a whitespace-separated `LIBRARIES` value into repeated `--library` flags. `make fetch-port-debs` must consume the selection manifest exactly as written and must not accept an independent library filter, because Phase 1 must have one authoritative selection artifact per run.
- Add `tools/validator_selection.py` as the selector CLI with these functions:
  - `fetch_site_data(site_url: str) -> dict`
  - `find_proof(site_data: dict, mode: str) -> dict`
  - `is_fully_passing(library_entry: dict) -> bool`
  - `select_libraries(proof: dict, requested_libraries: list[str]) -> list[dict]`
  - `build_selection_manifest(site_url: str, proof: dict, requested_libraries: list[str]) -> dict`
- `validator_selection.py` must:
  - Fetch the live JSON with `urllib.request`.
  - Require `schema_version == 2`.
  - Find the proof object for `mode == "port-04-test"` and fail clearly if it is missing.
  - Select a library only when `totals.failed == 0` and `totals.passed == totals.cases`.
  - Preserve the live library ordering from the validator payload.
  - Carry forward the fields later phases need instead of stripping them out. Each library record in `dist/validator-selection.json` should retain `library`, `apt_packages`, `totals`, `port_repository`, `port_tag_ref`, `port_commit`, `port_release_tag`, `port_debs`, and `unported_original_packages`.
  - Support repeated `--library` filters for smoke runs and manual dry runs. If a requested library is absent from the current fully passing set, exit nonzero instead of silently dropping it.
  - Record whether the manifest represents the full live set or a filtered subset. Use a field such as `selection_scope` with values `full` or `filtered`, because later publish logic must refuse filtered plans.
  - Preserve the proof-level `suite` object exactly as emitted by the validator instead of flattening `image` or `apt_suite` into unrelated top-level keys.
- `dist/validator-selection.json` should be deterministic and explicit. Its top-level fields should include at least `site_url`, `schema_version`, `proof_version`, `selected_mode`, `suite`, `proof_totals`, `selection_scope`, `requested_libraries`, and `libraries`. Do not add fetch timestamps or other run-specific noise that would make diffs unstable.
- Add `tools/fetch_port_debs.py` as the artifact downloader and locker with these functions:
  - `build_release_asset_url(port_repository: str, port_release_tag: str, filename: str) -> str`
  - `download_file(url: str, destination: Path) -> None`
  - `inspect_deb(path: Path) -> dict`
  - `lock_library_debs(library_entry: dict, output_root: Path) -> dict`
  - `build_port_deb_lock(selection_manifest: dict, output_root: Path) -> dict`
- `fetch_port_debs.py` must consume `dist/validator-selection.json` only. It must not refetch `site-data.json`, call the GitHub API, list Git tags, or derive a new release tag. The validator has already done that work.
- Download URLs should be derived directly from the validator-supplied metadata with the pattern `https://github.com/{port_repository}/releases/download/{port_release_tag}/{filename}`.
- For each expected `.deb`, `fetch_port_debs.py` should:
  - Materialize it under `.work/debs/port-04-test/<library>/`.
  - Reuse an existing local file when its SHA-256 and size already match the validator metadata.
  - Otherwise download to a temporary path, retry transient network failures a small fixed number of times, and atomically rename into place.
  - Verify the SHA-256 matches the value in the validator's `port_debs` entry.
  - Verify the file size if the validator provided one.
  - Read `Package`, `Version`, and `Architecture` from `dpkg-deb --field`.
  - Require the observed package name to match the validator-declared package.
  - Require the architecture to be `amd64` or `all`.
- `dist/port-debs-lock.json` should keep the selection metadata and add local materialization details so later phases do not need to reopen the selection manifest. Its top-level fields should include at least `site_url`, `schema_version`, `proof_version`, `selected_mode`, `suite`, `selection_scope`, `requested_libraries`, and `libraries`. Each locked deb entry should include `package`, `version`, `architecture`, `filename`, `sha256`, `size`, `source_url`, and repo-relative `local_path`.
- If the validator says a library is fully passing but any expected `.deb` is missing, corrupt, or metadata-mismatched, Phase 1 must fail hard. The repository must not silently skip a library that the validator currently considers eligible.
- Keep tests offline. Use checked-in JSON fixtures plus `unittest.mock` for HTTP, filesystem, and subprocess behavior. Cover:
  - Selection of only fully passing libraries using the real schema keys `mode`, `suite`, `passed`, `failed`, and `cases`
  - Rejection of missing `port-04-test` proofs
  - Rejection of unexpected schema versions
  - Subset-filter validation
  - Release URL construction from `port_repository`, `port_release_tag`, and `filename`
  - Reuse of already validated local `.deb` files
  - Failure on SHA mismatch, package mismatch, or architecture mismatch

## Verification Phases
### `check-validator-selection`
- Phase ID: `check-validator-selection`
- Type: `check`
- `bounce_target`: `implement-validator-selection`
- Purpose: Parse the live validator JSON, select only fully passing libraries from `port-04-test`, and materialize the exact `.deb` assets identified by validator metadata.
- Commands:

```sh
python3 -m unittest discover -s tests -v
make resolve-validator
make fetch-port-debs
```

## Success Criteria
- `dist/validator-selection.json` is produced from the live validator payload, keyed by `mode == "port-04-test"`, and contains only fully passing libraries.
- `dist/validator-selection.json` preserves the validator `suite` object plus `port_repository`, `port_release_tag`, `port_commit`, and `port_debs` for every selected library.
- `dist/port-debs-lock.json` is produced from `dist/validator-selection.json` without rediscovering release metadata and records repo-relative `local_path` values.
- `.work/debs/port-04-test/` contains every validator-listed `.deb` for the selected libraries, and each file matches validator checksum and size metadata.
- The Phase 1 Python tooling and Makefile wrappers stay standard-library-only, use module-style CLI invocation, and preserve the existing `clean` behavior for `.work/` and `dist/`.
- The offline unit tests and both Makefile verification commands pass.

## Git Commit Requirement
The generated implement prompt for this phase must instruct the implementer to commit all Phase 1 work to git before yielding.
