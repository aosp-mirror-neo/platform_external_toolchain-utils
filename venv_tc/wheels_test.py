# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for wheels."""

import hashlib
import multiprocessing.pool
from pathlib import Path
from unittest import mock

from llvm_tools import test_helpers
from venv_tc import wheels


class WheelsTest(test_helpers.TempDirTestCase):
    """Tests for the wheels script."""

    def setUp(self) -> None:
        self.tempdir = self.make_tempdir()
        self.venv_dir = self.tempdir / "venv"
        self.venv_dir.mkdir()
        self.wheel_dir = self.venv_dir / "wheels"
        self.wheel_dir.mkdir()

        # gs-related methods are mocked here. By default, `raise` if they're
        # called, since tests should have to explicitly account for
        # side-effects. This is preferable to only patching them sometimes, and
        # having tests start actually calling `gsutil ...`.
        called_but_not_mocked = AssertionError(
            "gs:// function called but not mocked"
        )

        patcher_upload = mock.patch.object(wheels, "upload_new_wheels_to_gs")
        self.mock_upload = patcher_upload.start()
        self.mock_upload.side_effect = called_but_not_mocked
        self.addCleanup(patcher_upload.stop)

        patcher_fetch = mock.patch.object(
            wheels, "fetch_wheels_from_gs_overwriting"
        )
        self.mock_fetch = patcher_fetch.start()
        self.mock_fetch.side_effect = called_but_not_mocked
        self.addCleanup(patcher_fetch.stop)

        patcher_list_gs = mock.patch.object(wheels, "list_wheels_in_gs")
        self.mock_list_gs = patcher_list_gs.start()
        self.mock_list_gs.side_effect = called_but_not_mocked
        self.addCleanup(patcher_list_gs.stop)

        patcher_populate = mock.patch.object(
            wheels, "populate_wheels_subdir_with_pip"
        )
        self.mock_populate = patcher_populate.start()
        self.mock_populate.side_effect = called_but_not_mocked
        self.addCleanup(patcher_populate.stop)

    def test_manifest_read_write(self) -> None:
        manifest = wheels.WheelManifest(
            wheel_hashes={"wheel1": "hash1", "wheel2": "hash2"}
        )
        wheels.write_wheel_manifest(self.venv_dir, manifest)
        read_manifest = wheels.read_wheel_manifest(self.venv_dir)
        self.assertEqual(manifest, read_manifest)

    def test_calculate_wheel_hash(self) -> None:
        wheel_file = self.wheel_dir / "mywheel.whl"
        wheel_content = b"some wheel content"
        wheel_file.write_bytes(wheel_content)

        hasher = hashlib.sha512()
        hasher.update(wheel_content)
        expected_hash = hasher.hexdigest()

        self.assertEqual(wheels.calculate_wheel_hash(wheel_file), expected_hash)

    def test_calculate_wheel_hash_file_not_found(self) -> None:
        self.assertIsNone(
            wheels.calculate_wheel_hash(self.wheel_dir / "nonexistent.whl")
        )

    def test_generate_wheel_manifest(self) -> None:
        wheel1_file = self.wheel_dir / "wheel1.whl"
        wheel1_content = b"content1"
        wheel1_file.write_bytes(wheel1_content)
        wheel1_hash = hashlib.sha512(wheel1_content).hexdigest()

        wheel2_file = self.wheel_dir / "wheel2.whl"
        wheel2_content = b"content2"
        wheel2_file.write_bytes(wheel2_content)
        wheel2_hash = hashlib.sha512(wheel2_content).hexdigest()

        with multiprocessing.pool.ThreadPool() as pool:
            manifest = wheels.generate_wheel_manifest(self.wheel_dir, pool)

        expected_manifest = wheels.WheelManifest(
            wheel_hashes={
                "wheel1.whl": wheel1_hash,
                "wheel2.whl": wheel2_hash,
            }
        )
        self.assertEqual(manifest, expected_manifest)

    def test_validate_one_wheel_file(self) -> None:
        wheel_name = "wheel.whl"
        wheel_file = self.wheel_dir / wheel_name
        wheel_content = b"content"
        wheel_file.write_bytes(wheel_content)
        wheel_hash = hashlib.sha512(wheel_content).hexdigest()

        manifest = wheels.WheelManifest(wheel_hashes={wheel_name: wheel_hash})

        # Valid wheel
        self.assertIsNone(
            wheels.validate_one_wheel_file(wheel_name, self.wheel_dir, manifest)
        )

        # Missing wheel
        wheel_file.unlink()
        self.assertEqual(
            wheels.validate_one_wheel_file(
                wheel_name, self.wheel_dir, manifest
            ),
            wheel_name,
        )

        # Mismatched hash
        wheel_file.write_bytes(b"different content")
        self.assertEqual(
            wheels.validate_one_wheel_file(
                wheel_name, self.wheel_dir, manifest
            ),
            wheel_name,
        )
        # The file should be deleted
        self.assertFalse(wheel_file.exists())

    def test_update_wheels_and_manifest_no_upload(self) -> None:
        def fake_populate(_: Path, wheel_dir: Path) -> None:
            wheel_dir.mkdir()
            # Create some fake wheels
            (wheel_dir / "new_wheel.whl").touch()
            (wheel_dir / "existing_wheel.whl").touch()

        self.mock_populate.side_effect = fake_populate
        self.mock_list_gs.side_effect = None
        self.mock_list_gs.return_value = [
            "existing_wheel.whl",
            "other_gs_wheel.whl",
        ]
        self.mock_fetch.side_effect = None

        wheels.update_wheels_and_manifest(self.venv_dir, upload=False)

        self.mock_populate.assert_called_once_with(
            self.venv_dir, self.wheel_dir
        )
        self.mock_list_gs.assert_called_once()
        self.mock_fetch.assert_called_once_with(
            self.wheel_dir, ["existing_wheel.whl"]
        )

    def test_update_wheels_and_manifest_with_upload(self) -> None:
        def fake_populate(_: Path, wheel_dir: Path) -> None:
            wheel_dir.mkdir()
            (wheel_dir / "wheel.whl").touch()

        self.mock_populate.side_effect = fake_populate
        self.mock_list_gs.side_effect = None
        self.mock_list_gs.return_value = []
        self.mock_upload.side_effect = None

        wheels.update_wheels_and_manifest(self.venv_dir, upload=True)

        self.mock_populate.assert_called_once_with(
            self.venv_dir, self.wheel_dir
        )
        self.mock_list_gs.assert_called_once()
        self.mock_fetch.assert_not_called()
        self.mock_upload.assert_called_once_with(self.wheel_dir)

    def test_ensure_downloaded_all_valid(self) -> None:
        wheel_name = "wheel.whl"
        wheel_file = self.wheel_dir / wheel_name
        wheel_content = b"content"
        wheel_file.write_bytes(wheel_content)
        wheel_hash = hashlib.sha512(wheel_content).hexdigest()

        manifest = wheels.WheelManifest(wheel_hashes={wheel_name: wheel_hash})
        wheels.write_wheel_manifest(self.venv_dir, manifest)

        wheels.ensure_downloaded(self.venv_dir, clean=False)

        self.mock_fetch.assert_not_called()

    def test_ensure_downloaded_missing_and_invalid_wheels(self) -> None:
        # Setup manifest
        valid_wheel_name = "valid.whl"
        valid_content = b"valid"
        valid_hash = hashlib.sha512(valid_content).hexdigest()

        invalid_wheel_name = "invalid.whl"
        invalid_content = b"invalid"
        invalid_hash = hashlib.sha512(invalid_content).hexdigest()

        missing_wheel_name = "missing.whl"
        missing_content = b"missing"
        missing_hash = hashlib.sha512(missing_content).hexdigest()

        manifest = wheels.WheelManifest(
            wheel_hashes={
                valid_wheel_name: valid_hash,
                invalid_wheel_name: invalid_hash,
                missing_wheel_name: missing_hash,
            }
        )
        wheels.write_wheel_manifest(self.venv_dir, manifest)

        # Setup wheel files
        (self.wheel_dir / valid_wheel_name).write_bytes(valid_content)
        (self.wheel_dir / invalid_wheel_name).write_bytes(b"wrong content")

        # after fetch, the files should be valid
        def fake_fetch(wheel_dir: Path, broken_files: list[str]) -> None:
            self.assertIn(invalid_wheel_name, broken_files)
            self.assertIn(missing_wheel_name, broken_files)
            (wheel_dir / invalid_wheel_name).write_bytes(invalid_content)
            (wheel_dir / missing_wheel_name).write_bytes(missing_content)

        self.mock_fetch.side_effect = fake_fetch

        wheels.ensure_downloaded(self.venv_dir, clean=False)

        self.mock_fetch.assert_called_once()
        # Use a set for order-independent comparison
        broken_files_arg = set(self.mock_fetch.call_args[0][1])
        self.assertEqual(
            broken_files_arg, {invalid_wheel_name, missing_wheel_name}
        )
