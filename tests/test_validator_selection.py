from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools import read_json
from tools.validator_selection import (
    build_selection_manifest,
    fetch_site_data,
    find_proof,
    select_libraries,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class _MockResponse(io.BytesIO):
    def __enter__(self) -> "_MockResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class ValidatorSelectionTests(TestCase):
    def setUp(self) -> None:
        self.site_data = read_json(FIXTURES / "validator-site-data.json")
        self.selection_manifest = read_json(FIXTURES / "validator-selection.json")

    @patch("tools.validator_selection.urllib.request.urlopen")
    def test_fetch_site_data_accepts_schema_version_two(self, mock_urlopen) -> None:
        payload = json.dumps({"schema_version": 2, "testcases": [], "proofs": []}).encode("utf-8")
        mock_urlopen.return_value = _MockResponse(payload)

        site_data = fetch_site_data("https://example.test/validator/site-data.json")

        self.assertEqual(2, site_data["schema_version"])

    @patch("tools.validator_selection.urllib.request.urlopen")
    def test_fetch_site_data_rejects_unexpected_schema_version(self, mock_urlopen) -> None:
        site_data = copy.deepcopy(self.site_data)
        site_data["schema_version"] = 3
        mock_urlopen.return_value = _MockResponse(json.dumps(site_data).encode("utf-8"))

        with self.assertRaisesRegex(ValueError, "schema_version"):
            fetch_site_data("https://example.test/validator/site-data.json")

    def test_find_proof_rejects_missing_port_04_test(self) -> None:
        site_data = copy.deepcopy(self.site_data)
        site_data["proofs"] = [proof for proof in site_data["proofs"] if proof["mode"] != "port-04-test"]

        with self.assertRaisesRegex(ValueError, "port-04-test"):
            find_proof(site_data, "port-04-test")

    def test_select_libraries_only_returns_fully_passing_entries(self) -> None:
        proof = find_proof(self.site_data, "port-04-test")

        selected = select_libraries(proof, [])

        self.assertEqual(["alpha", "gamma", "delta"], [entry["library"] for entry in selected])
        self.assertTrue(all(entry["totals"]["failed"] == 0 for entry in selected))
        self.assertTrue(
            all(entry["totals"]["passed"] == entry["totals"]["cases"] for entry in selected)
        )

    def test_select_libraries_preserves_validator_order_for_filtered_subset(self) -> None:
        proof = find_proof(self.site_data, "port-04-test")

        selected = select_libraries(proof, ["delta", "alpha"])

        self.assertEqual(["alpha", "delta"], [entry["library"] for entry in selected])

    def test_select_libraries_rejects_missing_subset_member(self) -> None:
        proof = find_proof(self.site_data, "port-04-test")

        with self.assertRaisesRegex(ValueError, "beta"):
            select_libraries(proof, ["alpha", "beta"])

    def test_build_selection_manifest_matches_fixture(self) -> None:
        proof = find_proof(self.site_data, "port-04-test")

        manifest = build_selection_manifest(
            "https://example.test/validator/site-data.json",
            proof,
            [],
        )

        self.assertEqual(self.selection_manifest, manifest)
