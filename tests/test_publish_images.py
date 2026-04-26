from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools import read_json
from tools.publish_images import assert_local_image_exists, publish_images, require_full_selection

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class PublishImagesTests(TestCase):
    def setUp(self) -> None:
        self.plan = read_json(FIXTURES / "image-build-plan.json")

    def test_require_full_selection_rejects_filtered_scope(self) -> None:
        filtered_plan = copy.deepcopy(self.plan)
        filtered_plan["selection_scope"] = "filtered"

        with self.assertRaisesRegex(ValueError, "selection_scope"):
            require_full_selection(filtered_plan)

    def test_require_full_selection_rejects_requested_libraries(self) -> None:
        filtered_plan = copy.deepcopy(self.plan)
        filtered_plan["requested_libraries"] = ["alpha"]

        with self.assertRaisesRegex(ValueError, "requested_libraries"):
            require_full_selection(filtered_plan)

    @patch("tools.publish_images.push_image")
    @patch("tools.publish_images.assert_local_image_exists")
    def test_publish_images_rejects_non_safelibs_namespace(
        self,
        mock_assert_local_image_exists,
        mock_push_image,
    ) -> None:
        plan = copy.deepcopy(self.plan)
        plan["image_namespace"] = "example"
        plan["images"][0]["image_ref"] = "example/alpha:latest"

        with self.assertRaisesRegex(ValueError, "safelibs namespace"):
            publish_images(plan)

        mock_assert_local_image_exists.assert_not_called()
        mock_push_image.assert_not_called()

    @patch("tools.publish_images.subprocess.run")
    def test_assert_local_image_exists_rejects_missing_image(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Error: No such image: safelibs/alpha:latest",
        )

        with self.assertRaisesRegex(FileNotFoundError, "safelibs/alpha:latest"):
            assert_local_image_exists("safelibs/alpha:latest")

    @patch("tools.publish_images.push_image")
    @patch("tools.publish_images.assert_local_image_exists")
    def test_publish_images_pushes_per_library_then_aggregate(
        self,
        mock_assert_local_image_exists,
        mock_push_image,
    ) -> None:
        plan = copy.deepcopy(self.plan)
        plan["images"] = [
            copy.deepcopy(self.plan["images"][2]),
            copy.deepcopy(self.plan["images"][-1]),
            copy.deepcopy(self.plan["images"][0]),
            copy.deepcopy(self.plan["images"][1]),
        ]
        events: list[tuple[str, str]] = []

        def record_exists(image_ref: str) -> None:
            events.append(("check", image_ref))

        def record_push(image_ref: str) -> None:
            events.append(("push", image_ref))

        mock_assert_local_image_exists.side_effect = record_exists
        mock_push_image.side_effect = record_push

        published_refs = publish_images(plan)

        expected_refs = [
            "safelibs/alpha:latest",
            "safelibs/gamma:latest",
            "safelibs/delta:latest",
            "safelibs/all:latest",
        ]
        self.assertEqual(expected_refs, published_refs)
        self.assertEqual(
            [("check", image_ref) for image_ref in expected_refs]
            + [("push", image_ref) for image_ref in expected_refs],
            events,
        )
