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

    @patch("tools.verify_images.query_installed_packages")
    def test_verify_image_requires_exact_versions(self, mock_query_installed_packages) -> None:
        mock_query_installed_packages.return_value = {"libalpha1": "1.0-1wrong"}

        with self.assertRaisesRegex(ValueError, "expected 1.0-1safelibs1 observed 1.0-1wrong"):
            verify_image(self.plan["images"][0])

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
