from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools import read_json
from tools.verify_images import query_installed_packages, verify_build_plan, verify_image

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class VerifyImagesTests(TestCase):
    def setUp(self) -> None:
        self.plan = read_json(FIXTURES / "image-build-plan.json")
        self.plan["base_image_id"] = "sha256:test-base"

    @patch("tools.verify_images.subprocess.run")
    def test_query_installed_packages_parses_dpkg_query_output(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="libalpha1\t1.0-1safelibs1\nlibgamma1\t2.0-1safelibs1\n",
            stderr="",
        )

        observed = query_installed_packages(
            "safelibs/all:latest",
            ["libalpha1", "libgamma1"],
        )

        self.assertEqual(
            {
                "libalpha1": "1.0-1safelibs1",
                "libgamma1": "2.0-1safelibs1",
            },
            observed,
        )

    @patch("tools.verify_images.subprocess.run")
    def test_query_installed_packages_tolerates_missing_requested_packages(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="libalpha1\t1.0-1safelibs1\n",
            stderr="dpkg-query: no packages found matching libdelta1\n",
        )

        observed = query_installed_packages(
            "safelibs/all:latest",
            ["libalpha1", "libdelta1"],
        )

        self.assertEqual({"libalpha1": "1.0-1safelibs1"}, observed)

    @patch("tools.verify_images._query_image_base_id")
    @patch("tools.verify_images._query_image_plan_digest")
    @patch("tools.verify_images.query_installed_packages")
    def test_verify_image_requires_exact_versions(
        self,
        mock_query_installed_packages,
        mock_query_image_plan_digest,
        mock_query_image_base_id,
    ) -> None:
        mock_query_image_plan_digest.return_value = self.plan["images"][0]["plan_digest"]
        mock_query_image_base_id.return_value = self.plan["base_image_id"]
        mock_query_installed_packages.return_value = {"libalpha1": "1.0-1wrong"}

        with self.assertRaisesRegex(ValueError, "expected 1.0-1safelibs1 observed 1.0-1wrong"):
            verify_image(self.plan["images"][0], expected_base_image_id=self.plan["base_image_id"])

    @patch("tools.verify_images._query_image_base_id")
    @patch("tools.verify_images._query_image_plan_digest")
    def test_verify_image_rejects_plan_digest_mismatch(
        self,
        mock_query_image_plan_digest,
        mock_query_image_base_id,
    ) -> None:
        mock_query_image_plan_digest.return_value = "different-plan-digest"
        mock_query_image_base_id.return_value = self.plan["base_image_id"]

        with self.assertRaisesRegex(ValueError, "plan digest"):
            verify_image(self.plan["images"][0], expected_base_image_id=self.plan["base_image_id"])

    @patch("tools.verify_images._query_image_base_id")
    @patch("tools.verify_images._query_image_plan_digest")
    @patch("tools.verify_images.query_installed_packages")
    def test_verify_image_checks_expected_packages_after_plan_digest(
        self,
        mock_query_installed_packages,
        mock_query_image_plan_digest,
        mock_query_image_base_id,
    ) -> None:
        mock_query_image_plan_digest.return_value = self.plan["images"][0]["plan_digest"]
        mock_query_image_base_id.return_value = self.plan["base_image_id"]
        mock_query_installed_packages.return_value = {"libalpha1": "1.0-1safelibs1"}

        verify_image(self.plan["images"][0], expected_base_image_id=self.plan["base_image_id"])

    @patch("tools.verify_images._query_image_base_id")
    @patch("tools.verify_images._query_image_plan_digest")
    def test_verify_image_rejects_base_image_id_mismatch(
        self,
        mock_query_image_plan_digest,
        mock_query_image_base_id,
    ) -> None:
        mock_query_image_plan_digest.return_value = self.plan["images"][0]["plan_digest"]
        mock_query_image_base_id.return_value = "sha256:stale-base"

        with self.assertRaisesRegex(ValueError, "base image id"):
            verify_image(self.plan["images"][0], expected_base_image_id=self.plan["base_image_id"])

    @patch("tools.verify_images.verify_image")
    def test_verify_build_plan_checks_filtered_request_metadata(self, mock_verify_image) -> None:
        filtered_plan = copy.deepcopy(self.plan)
        filtered_plan["selection_scope"] = "filtered"
        filtered_plan["requested_libraries"] = ["delta", "alpha"]
        filtered_plan["images"] = [
            copy.deepcopy(self.plan["images"][0]),
            copy.deepcopy(self.plan["images"][2]),
            copy.deepcopy(self.plan["images"][3]),
        ]
        filtered_plan["images"][0]["libraries"] = ["alpha"]
        filtered_plan["images"][1]["libraries"] = ["delta"]
        filtered_plan["images"][2]["libraries"] = ["alpha", "delta"]

        verify_build_plan(filtered_plan, ["delta", "alpha"])

        self.assertEqual(3, mock_verify_image.call_count)

    @patch("tools.verify_images._query_image_base_id")
    @patch("tools.verify_images._query_image_plan_digest")
    @patch("tools.verify_images.query_installed_packages")
    def test_verify_build_plan_rejects_filtered_aggregate_scope_mismatch(
        self,
        mock_query_installed_packages,
        mock_query_image_plan_digest,
        mock_query_image_base_id,
    ) -> None:
        filtered_plan = copy.deepcopy(self.plan)
        filtered_plan["selection_scope"] = "filtered"
        filtered_plan["requested_libraries"] = ["delta", "alpha"]
        filtered_plan["images"] = [
            copy.deepcopy(self.plan["images"][0]),
            copy.deepcopy(self.plan["images"][2]),
            copy.deepcopy(self.plan["images"][3]),
        ]
        filtered_plan["images"][0]["libraries"] = ["alpha"]
        filtered_plan["images"][1]["libraries"] = ["delta"]
        filtered_plan["images"][2]["libraries"] = ["alpha", "delta"]
        filtered_plan["images"][2]["packages"] = [
            copy.deepcopy(self.plan["images"][0]["packages"][0]),
            copy.deepcopy(self.plan["images"][2]["packages"][0]),
        ]
        filtered_plan["images"][2]["plan_digest"] = "filtered-plan-digest"

        def fake_query(image_ref: str, package_names: list[str]) -> dict[str, str]:
            if image_ref == "safelibs/all:latest":
                return {
                    "libalpha1": "1.0-1safelibs1",
                    "libdelta1": "3.0-1safelibs1",
                }
            if image_ref == "safelibs/alpha:latest":
                return {"libalpha1": "1.0-1safelibs1"}
            if image_ref == "safelibs/delta:latest":
                return {"libdelta1": "3.0-1safelibs1"}
            raise AssertionError(f"unexpected image_ref {image_ref}")

        def fake_digest(image_ref: str) -> str:
            if image_ref == "safelibs/all:latest":
                return "full-plan-digest"
            for image_spec in filtered_plan["images"][:2]:
                if image_spec["image_ref"] == image_ref:
                    return image_spec["plan_digest"]
            raise AssertionError(f"unexpected image_ref {image_ref}")

        mock_query_image_base_id.return_value = self.plan["base_image_id"]
        mock_query_installed_packages.side_effect = fake_query
        mock_query_image_plan_digest.side_effect = fake_digest

        with self.assertRaisesRegex(ValueError, "plan digest"):
            verify_build_plan(filtered_plan, ["delta", "alpha"])

    @patch("tools.verify_images.verify_image")
    def test_verify_build_plan_rejects_scope_mismatch(self, mock_verify_image) -> None:
        with self.assertRaisesRegex(ValueError, "selection_scope"):
            verify_build_plan(self.plan, ["alpha"])

        mock_verify_image.assert_not_called()

    @patch("tools.verify_images.verify_image")
    def test_verify_build_plan_requires_aggregate_image(self, mock_verify_image) -> None:
        plan = copy.deepcopy(self.plan)
        plan["images"] = plan["images"][:-1]

        with self.assertRaisesRegex(ValueError, "aggregate all image"):
            verify_build_plan(plan, [])

        mock_verify_image.assert_not_called()
