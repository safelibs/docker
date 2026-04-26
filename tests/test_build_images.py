from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from tools import ensure_directory, read_json, repo_relative_path, repo_root
from tools.build_images import (
    _IMAGE_SPECS_BY_REF,
    _requires_aggregate_fallback,
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
                "FROM ubuntu:24.04\n"
                "COPY debs/ /tmp/debs/\n"
                "RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y "
                "--no-install-recommends /tmp/debs/*.deb && rm -rf /var/lib/apt/lists/* /tmp/debs\n"
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

    def test_aggregate_runtime_fallback_uses_canonical_context_only(self) -> None:
        lock_data = self._materialize_locked_debs(self.lock_data)
        lock_data["libraries"][0]["library"] = "libxml"
        lock_data["libraries"][0]["port_debs"][0]["package"] = "libxml2"
        lock_data["libraries"][1]["library"] = "omega"
        plan = build_image_plan(
            lock_data,
            image_namespace="safelibs",
            base_image="ubuntu:24.04",
            requested_libraries=[],
        )
        aggregate_image = plan["images"][-1]
        context_dir = prepare_context(aggregate_image, self.temp_root / "contexts")

        self.assertTrue(_requires_aggregate_fallback(aggregate_image))
        _IMAGE_SPECS_BY_REF.clear()
        _IMAGE_SPECS_BY_REF[aggregate_image["image_ref"]] = aggregate_image
        build_failure = RuntimeError("shared-mime-info\nSegmentation fault")
        with patch("tools.build_images.uuid.uuid4") as mock_uuid, patch(
            "tools.build_images._docker_run"
        ) as mock_docker_run:
            mock_uuid.return_value.hex = "fixed"
            mock_docker_run.side_effect = [
                SimpleNamespace(returncode=1, stderr=str(build_failure), stdout=""),
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ]

            docker_build(aggregate_image["image_ref"], context_dir)

        calls = [call.args[0] for call in mock_docker_run.call_args_list]
        self.assertEqual(
            ["docker", "build", "--pull", "-t", aggregate_image["image_ref"], str(context_dir)],
            calls[0],
        )
        self.assertEqual(["docker", "pull", "ubuntu:24.04"], calls[1])
        self.assertEqual(
            ["docker", "run", "--name", "safelibs-build-fixed", "-d", "ubuntu:24.04", "sleep", "infinity"],
            calls[2],
        )
        self.assertEqual(
            ["docker", "cp", f"{context_dir / 'debs'}/.", "safelibs-build-fixed:/tmp/debs/"],
            calls[4],
        )
        joined_calls = "\n".join(" ".join(command) for command in calls)
        self.assertNotIn(".staged-build", joined_calls)
        self.assertIn(str(context_dir / "debs"), joined_calls)
        _IMAGE_SPECS_BY_REF.clear()

    def test_build_image_plan_rejects_base_image_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "BASE_IMAGE"):
            build_image_plan(
                self.lock_data,
                image_namespace="safelibs",
                base_image="ubuntu:22.04",
                requested_libraries=[],
            )
