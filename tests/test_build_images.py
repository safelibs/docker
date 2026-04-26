from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools import ensure_directory, read_json, repo_relative_path, repo_root
from tools.build_images import (
    BASE_IMAGE_ID_LABEL,
    PLAN_DIGEST_LABEL,
    _IMAGE_PLAN_DIGESTS_BY_REF,
    _aggregate_cache_image_ref,
    _build_execution_order,
    _local_image_matches,
    _preserve_full_aggregate_cache,
    _restore_full_aggregate_from_cache,
    build_image_plan,
    docker_build,
    prepare_context,
    render_dockerfile,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALPHA_BYTES = b"alpha-runtime-deb\n"
GAMMA_BYTES = b"gamma-runtime-deb\n"
DELTA_BYTES = b"delta-runtime-deb\n"
PACKAGE_BYTES = {
    "libalpha1_1.0-1safelibs1_amd64.deb": ALPHA_BYTES,
    "libgamma1_2.0-1safelibs1_amd64.deb": GAMMA_BYTES,
    "libdelta1_3.0-1safelibs1_all.deb": DELTA_BYTES,
}


class BuildImagesTests(TestCase):
    def setUp(self) -> None:
        self.lock_data = read_json(FIXTURES / "port-debs-lock.json")
        self.expected_plan = read_json(FIXTURES / "image-build-plan.json")
        ensure_directory(repo_root() / ".work")
        self._tempdir = tempfile.TemporaryDirectory(dir=str(repo_root() / ".work"))
        self.temp_root = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _materialize_locked_debs(self, lock_data: dict) -> dict:
        updated = copy.deepcopy(lock_data)
        for library_entry in updated["libraries"]:
            for package in library_entry["port_debs"]:
                package_path = self.temp_root / "debs" / library_entry["library"] / package["filename"]
                package_path.parent.mkdir(parents=True, exist_ok=True)
                package_path.write_bytes(PACKAGE_BYTES[package["filename"]])
                package["local_path"] = repo_relative_path(package_path)
        return updated

    def test_render_dockerfile_matches_contract(self) -> None:
        self.assertEqual(
            (
                "# syntax=docker/dockerfile:1\n"
                "FROM ubuntu:24.04\n"
                "COPY debs/ /tmp/debs/\n"
                "RUN --mount=type=cache,target=/var/cache/apt,sharing=locked "
                "--mount=type=cache,target=/var/lib/apt,sharing=locked "
                "set -eux; rm -f /etc/apt/apt.conf.d/docker-clean; "
                "for attempt in 1 2 3; do apt-get update -o Acquire::Retries=3 && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y -o Acquire::Retries=3 "
                "-o DPkg::Use-Pty=0 --no-install-recommends /tmp/debs/*.deb && break; "
                "if [ \"$attempt\" -eq 3 ]; then exit 1; fi; rm -rf /var/lib/apt/lists/*; "
                "sleep \"$attempt\"; done; rm -rf /tmp/debs\n"
            ),
            render_dockerfile("ubuntu:24.04"),
        )

    def test_build_image_plan_matches_fixture(self) -> None:
        plan = build_image_plan(
            self.lock_data,
            image_namespace="safelibs",
            base_image="ubuntu:24.04",
            requested_libraries=[],
        )

        self.assertEqual(self.expected_plan, plan)

    def test_build_image_plan_marks_filtered_selection_and_preserves_lock_order(self) -> None:
        plan = build_image_plan(
            self.lock_data,
            image_namespace="safelibs",
            base_image="ubuntu:24.04",
            requested_libraries=["delta", "alpha"],
        )

        self.assertEqual("filtered", plan["selection_scope"])
        self.assertEqual(["delta", "alpha"], plan["requested_libraries"])
        self.assertEqual(
            [
                "safelibs/alpha:latest",
                "safelibs/delta:latest",
                "safelibs/all:latest",
            ],
            [image["image_ref"] for image in plan["images"]],
        )
        self.assertEqual(["alpha", "delta"], plan["images"][-1]["libraries"])

    def test_build_image_plan_rejects_duplicate_package_conflicts(self) -> None:
        lock_data = copy.deepcopy(self.lock_data)
        lock_data["libraries"][1]["port_debs"][0] = {
            **lock_data["libraries"][1]["port_debs"][0],
            "package": "libalpha1",
            "version": "9.9-1",
            "filename": "libalpha1_9.9-1_amd64.deb",
            "sha256": "deadbeef" * 8,
            "size": 99,
        }

        with self.assertRaisesRegex(ValueError, "package conflict"):
            build_image_plan(
                lock_data,
                image_namespace="safelibs",
                base_image="ubuntu:24.04",
                requested_libraries=[],
            )

    def test_build_image_plan_dedupes_identical_aggregate_packages(self) -> None:
        lock_data = copy.deepcopy(self.lock_data)
        duplicate_package = copy.deepcopy(lock_data["libraries"][0]["port_debs"][0])
        lock_data["libraries"][1]["port_debs"].append(duplicate_package)

        plan = build_image_plan(
            lock_data,
            image_namespace="safelibs",
            base_image="ubuntu:24.04",
            requested_libraries=[],
        )

        all_packages = [package["package"] for package in plan["images"][-1]["packages"]]
        self.assertEqual(["libalpha1", "libgamma1", "libdelta1"], all_packages)

    def test_prepare_context_is_deterministic_and_updates_context_dir(self) -> None:
        lock_data = self._materialize_locked_debs(self.lock_data)
        plan = build_image_plan(
            lock_data,
            image_namespace="safelibs",
            base_image="ubuntu:24.04",
            requested_libraries=["alpha"],
        )
        image_spec = plan["images"][0]
        context_root = self.temp_root / "contexts"
        stale_dir = context_root / "alpha"
        stale_dir.mkdir(parents=True, exist_ok=True)
        (stale_dir / "stale.txt").write_text("stale\n", encoding="utf-8")

        context_dir = prepare_context(image_spec, context_root)

        self.assertEqual(context_root / "alpha", context_dir)
        self.assertEqual(repo_relative_path(context_dir), image_spec["context_dir"])
        self.assertFalse((context_dir / "stale.txt").exists())
        self.assertEqual(
            render_dockerfile("ubuntu:24.04"),
            (context_dir / "Dockerfile").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            ALPHA_BYTES,
            (context_dir / "debs" / "libalpha1_1.0-1safelibs1_amd64.deb").read_bytes(),
        )

    def test_build_image_plan_keeps_aggregate_metadata_contract_without_install_groups(self) -> None:
        lock_data = copy.deepcopy(self.lock_data)
        lock_data["libraries"][0]["library"] = "libxml"
        lock_data["libraries"][1]["library"] = "omega"

        plan = build_image_plan(
            lock_data,
            image_namespace="safelibs",
            base_image="ubuntu:24.04",
            requested_libraries=[],
        )

        self.assertNotIn("install_groups", plan["images"][-1])
        self.assertEqual(["libalpha1", "libgamma1", "libdelta1"], [
            package["package"] for package in plan["images"][-1]["packages"]
        ])

    def test_prepare_context_keeps_single_debs_directory_for_aggregate_image(self) -> None:
        lock_data = self._materialize_locked_debs(self.lock_data)
        lock_data["libraries"][0]["library"] = "libxml"
        lock_data["libraries"][1]["library"] = "omega"
        plan = build_image_plan(
            lock_data,
            image_namespace="safelibs",
            base_image="ubuntu:24.04",
            requested_libraries=[],
        )

        context_dir = prepare_context(plan["images"][-1], self.temp_root / "contexts")

        self.assertTrue((context_dir / "debs").is_dir())
        self.assertFalse((context_dir / "debs-01-primary").exists())
        self.assertFalse((context_dir / "debs-02-deferred").exists())
        dockerfile = (context_dir / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(render_dockerfile("ubuntu:24.04"), dockerfile)

    def test_build_execution_order_moves_aggregate_first(self) -> None:
        prepared_contexts = [
            ({"image_ref": "safelibs/alpha:latest"}, self.temp_root / "contexts" / "alpha"),
            ({"image_ref": "safelibs/all:latest"}, self.temp_root / "contexts" / "all"),
            ({"image_ref": "safelibs/delta:latest"}, self.temp_root / "contexts" / "delta"),
        ]

        ordered = _build_execution_order(prepared_contexts)

        self.assertEqual(
            [
                "safelibs/all:latest",
                "safelibs/alpha:latest",
                "safelibs/delta:latest",
            ],
            [image_spec["image_ref"] for image_spec, _ in ordered],
        )

    def test_docker_build_uses_plain_context_dir(self) -> None:
        context_dir = self.temp_root / "contexts" / "all"
        context_dir.mkdir(parents=True)

        with patch.dict(
            _IMAGE_PLAN_DIGESTS_BY_REF,
            {"safelibs/all:latest": "digest-all"},
            clear=True,
        ), patch("tools.build_images._docker_run") as mock_docker_run, patch(
            "tools.build_images._CURRENT_BASE_IMAGE_ID",
            "sha256:test-base",
        ):
            docker_build("safelibs/all:latest", context_dir)

        mock_docker_run.assert_called_once_with(
            [
                "docker",
                "build",
                "--pull",
                "--label",
                f"{PLAN_DIGEST_LABEL}=digest-all",
                "--label",
                f"{BASE_IMAGE_ID_LABEL}=sha256:test-base",
                "-t",
                "safelibs/all:latest",
                str(context_dir),
            ],
            env={"DOCKER_BUILDKIT": "1"},
        )

    @patch("tools.build_images._query_image_base_id")
    @patch("tools.build_images._query_image_plan_digest")
    @patch("tools.build_images._query_installed_packages")
    @patch("tools.build_images._local_image_exists")
    def test_local_image_match_rejects_plan_digest_mismatch(
        self,
        mock_local_image_exists,
        mock_query_installed_packages,
        mock_query_image_plan_digest,
        mock_query_image_base_id,
    ) -> None:
        mock_local_image_exists.return_value = True
        mock_query_image_plan_digest.return_value = "full-plan-digest"
        mock_query_image_base_id.return_value = "sha256:test-base"
        mock_query_installed_packages.return_value = {
            "libalpha1": "1.0-1safelibs1",
            "libdelta1": "3.0-1safelibs1",
        }
        image_spec = {
            "image_ref": "safelibs/all:latest",
            "plan_digest": "filtered-plan-digest",
            "packages": [
                {"package": "libalpha1", "version": "1.0-1safelibs1"},
                {"package": "libdelta1", "version": "3.0-1safelibs1"},
            ],
        }

        with patch("tools.build_images._CURRENT_BASE_IMAGE_ID", "sha256:test-base"):
            self.assertFalse(_local_image_matches(image_spec))

    @patch("tools.build_images._query_image_base_id")
    @patch("tools.build_images._query_image_plan_digest")
    @patch("tools.build_images._query_installed_packages")
    @patch("tools.build_images._local_image_exists")
    def test_local_image_match_accepts_matching_plan_digest_and_versions(
        self,
        mock_local_image_exists,
        mock_query_installed_packages,
        mock_query_image_plan_digest,
        mock_query_image_base_id,
    ) -> None:
        mock_local_image_exists.return_value = True
        mock_query_image_plan_digest.return_value = "matching-plan-digest"
        mock_query_image_base_id.return_value = "sha256:test-base"
        mock_query_installed_packages.return_value = {
            "libalpha1": "1.0-1safelibs1",
        }
        image_spec = {
            "image_ref": "safelibs/alpha:latest",
            "plan_digest": "matching-plan-digest",
            "packages": [
                {"package": "libalpha1", "version": "1.0-1safelibs1"},
            ],
        }

        with patch("tools.build_images._CURRENT_BASE_IMAGE_ID", "sha256:test-base"):
            self.assertTrue(_local_image_matches(image_spec))

    @patch("tools.build_images._query_image_base_id")
    @patch("tools.build_images._query_image_plan_digest")
    @patch("tools.build_images._query_installed_packages")
    @patch("tools.build_images._local_image_exists")
    def test_local_image_match_rejects_base_image_id_mismatch(
        self,
        mock_local_image_exists,
        mock_query_installed_packages,
        mock_query_image_plan_digest,
        mock_query_image_base_id,
    ) -> None:
        mock_local_image_exists.return_value = True
        mock_query_image_plan_digest.return_value = "matching-plan-digest"
        mock_query_image_base_id.return_value = "sha256:stale-base"
        mock_query_installed_packages.return_value = {"libalpha1": "1.0-1safelibs1"}
        image_spec = {
            "image_ref": "safelibs/alpha:latest",
            "plan_digest": "matching-plan-digest",
            "packages": [
                {"package": "libalpha1", "version": "1.0-1safelibs1"},
            ],
        }

        with patch("tools.build_images._CURRENT_BASE_IMAGE_ID", "sha256:test-base"):
            self.assertFalse(_local_image_matches(image_spec))

    def test_build_image_plan_rejects_base_image_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "BASE_IMAGE"):
            build_image_plan(
                self.lock_data,
                image_namespace="safelibs",
                base_image="ubuntu:22.04",
                requested_libraries=[],
            )

    @patch("tools.build_images._docker_tag")
    @patch("tools.build_images._local_image_matches")
    def test_preserve_full_aggregate_cache_tags_latest_when_cache_missing(
        self,
        mock_local_image_matches,
        mock_docker_tag,
    ) -> None:
        image_spec = {
            "image_ref": "safelibs/all:latest",
            "packages": [],
        }
        mock_local_image_matches.return_value = False

        _preserve_full_aggregate_cache(image_spec)

        mock_docker_tag.assert_called_once_with(
            "safelibs/all:latest",
            _aggregate_cache_image_ref("safelibs/all:latest"),
        )

    @patch("tools.build_images._docker_tag")
    @patch("tools.build_images._local_image_matches")
    def test_restore_full_aggregate_from_cache_retags_latest(
        self,
        mock_local_image_matches,
        mock_docker_tag,
    ) -> None:
        image_spec = {
            "image_ref": "safelibs/all:latest",
            "packages": [],
        }
        mock_local_image_matches.return_value = True

        restored = _restore_full_aggregate_from_cache(image_spec)

        self.assertTrue(restored)
        mock_docker_tag.assert_called_once_with(
            _aggregate_cache_image_ref("safelibs/all:latest"),
            "safelibs/all:latest",
        )
