from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools import ensure_directory, read_json, repo_root
from tools.fetch_port_debs import (
    build_port_deb_lock,
    build_release_asset_url,
    inspect_deb,
    lock_library_debs,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALPHA_BYTES = b"alpha-runtime-deb\n"


class FetchPortDebsTests(TestCase):
    def setUp(self) -> None:
        self.selection_manifest = read_json(FIXTURES / "validator-selection.json")
        ensure_directory(repo_root() / ".work")
        self._tempdir = tempfile.TemporaryDirectory(dir=str(repo_root() / ".work"))
        self.output_root = Path(self._tempdir.name) / "debs"
        self.alpha_library = self.selection_manifest["libraries"][0]

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_build_release_asset_url_uses_validator_release_metadata(self) -> None:
        url = build_release_asset_url(
            "safelibs/port-alpha",
            "build-alpha1234",
            "libalpha1_1.0-1safelibs1_amd64.deb",
        )

        self.assertEqual(
            "https://github.com/safelibs/port-alpha/releases/download/build-alpha1234/"
            "libalpha1_1.0-1safelibs1_amd64.deb",
            url,
        )

    @patch("tools.fetch_port_debs.run_checked")
    def test_inspect_deb_reads_package_version_and_architecture(self, mock_run_checked) -> None:
        mock_run_checked.return_value = (
            "Package: libalpha1\n"
            "Version: 1.0-1safelibs1\n"
            "Architecture: amd64\n"
        )

        metadata = inspect_deb(Path("/tmp/libalpha1.deb"))

        self.assertEqual(
            {
                "package": "libalpha1",
                "version": "1.0-1safelibs1",
                "architecture": "amd64",
            },
            metadata,
        )

    @patch("tools.fetch_port_debs.inspect_deb")
    @patch("tools.fetch_port_debs.download_file")
    def test_lock_library_debs_reuses_already_validated_local_file(
        self,
        mock_download_file,
        mock_inspect_deb,
    ) -> None:
        filename = self.alpha_library["port_debs"][0]["filename"]
        destination = self.output_root / self.alpha_library["library"] / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(ALPHA_BYTES)
        mock_inspect_deb.return_value = {
            "package": "libalpha1",
            "version": "1.0-1safelibs1",
            "architecture": "amd64",
        }

        locked_library = lock_library_debs(self.alpha_library, self.output_root)

        mock_download_file.assert_not_called()
        self.assertEqual(
            destination.relative_to(repo_root()).as_posix(),
            locked_library["port_debs"][0]["local_path"],
        )
        self.assertEqual("1.0-1safelibs1", locked_library["port_debs"][0]["version"])

    @patch("tools.fetch_port_debs.download_file")
    def test_lock_library_debs_rejects_sha_mismatch(self, mock_download_file) -> None:
        def write_wrong_bytes(_url: str, destination: Path) -> None:
            destination.write_bytes(b"wrong-alpha-runtime\n")

        mock_download_file.side_effect = write_wrong_bytes

        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            lock_library_debs(self.alpha_library, self.output_root)

    @patch("tools.fetch_port_debs.inspect_deb")
    @patch("tools.fetch_port_debs.download_file")
    def test_lock_library_debs_rejects_package_mismatch(
        self,
        mock_download_file,
        mock_inspect_deb,
    ) -> None:
        mock_download_file.side_effect = lambda _url, destination: destination.write_bytes(ALPHA_BYTES)
        mock_inspect_deb.return_value = {
            "package": "wrong-package",
            "version": "1.0-1safelibs1",
            "architecture": "amd64",
        }

        with self.assertRaisesRegex(ValueError, "Package mismatch"):
            lock_library_debs(self.alpha_library, self.output_root)

    @patch("tools.fetch_port_debs.inspect_deb")
    @patch("tools.fetch_port_debs.download_file")
    def test_lock_library_debs_rejects_architecture_mismatch(
        self,
        mock_download_file,
        mock_inspect_deb,
    ) -> None:
        mock_download_file.side_effect = lambda _url, destination: destination.write_bytes(ALPHA_BYTES)
        mock_inspect_deb.return_value = {
            "package": "libalpha1",
            "version": "1.0-1safelibs1",
            "architecture": "arm64",
        }

        with self.assertRaisesRegex(ValueError, "architecture"):
            lock_library_debs(self.alpha_library, self.output_root)

    @patch("tools.fetch_port_debs.inspect_deb")
    @patch("tools.fetch_port_debs.download_file")
    def test_build_port_deb_lock_keeps_selection_metadata(
        self,
        mock_download_file,
        mock_inspect_deb,
    ) -> None:
        mock_download_file.side_effect = lambda _url, destination: destination.write_bytes(ALPHA_BYTES)
        mock_inspect_deb.return_value = {
            "package": "libalpha1",
            "version": "1.0-1safelibs1",
            "architecture": "amd64",
        }
        filtered_manifest = {
            **self.selection_manifest,
            "selection_scope": "filtered",
            "requested_libraries": ["alpha"],
            "libraries": [self.alpha_library],
        }

        lock_manifest = build_port_deb_lock(filtered_manifest, self.output_root)

        self.assertEqual("filtered", lock_manifest["selection_scope"])
        self.assertEqual(["alpha"], lock_manifest["requested_libraries"])
        self.assertEqual(1, len(lock_manifest["libraries"]))
