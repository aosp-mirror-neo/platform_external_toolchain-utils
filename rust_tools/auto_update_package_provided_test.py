# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for auto_update_package_provided."""

import subprocess
import textwrap
from unittest import mock

from llvm_tools import test_helpers
from rust_tools import auto_update_package_provided


class AutoUpdatePackageProvidedTest(test_helpers.TempDirTestCase):
    """Tests for the auto_update_package_provided script."""

    @mock.patch.object(subprocess, "run")
    def test_get_rust_version_success(self, mock_run):
        """Test successful rust version retrieval."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=("cros_sdk", "--", "rustc", "--version"),
            returncode=0,
            stdout=textwrap.dedent(
                """
                Some unrelated text from the SDK entering
                bits
                rustc 1.80.0-nightly (123456789 2025-01-01)
                """
            ),
            stderr="",
        )
        version = auto_update_package_provided.fetch_chroot_rust_version()
        self.assertEqual(
            version,
            auto_update_package_provided.rust_uprev.RustVersion(1, 80, 0),
        )

    def test_update_package_provided_file(self):
        """Test updating a package.provided file."""
        temp_file = self.make_tempdir() / "package.provided"
        # For the "broken version, should persist" strings, just let a human
        # handle resolving it. At the time of writing, Rust's upstream version
        # is strictly `major.minor.patch`.
        temp_file.write_text(
            textwrap.dedent(
                """
                # Comment
                dev-lang/rust-1.77.0.1  # broken version, should persist
                dev-lang/rust-1.77.0  # comment 2
                dev-lang/rust-1.78.0
                dev-lang/rust-1.79.0
                dev-lang/rust-host-1.78.0
                dev-lang/rust-host-1.79.0
                other-package/some-package-1.2.3
                """
            ),
            encoding="utf-8",
        )
        expected_content = textwrap.dedent(
            """
            # Comment
            dev-lang/rust-1.77.0.1  # broken version, should persist
            dev-lang/rust-1.79.0
            dev-lang/rust-host-1.79.0
            other-package/some-package-1.2.3
            """
        )

        version = auto_update_package_provided.rust_uprev.RustVersion.parse(
            "1.79.0"
        )
        changed = auto_update_package_provided.update_package_provided_file(
            temp_file, version, dry_run=False
        )

        self.assertTrue(changed)
        self.assertEqual(
            temp_file.read_text(encoding="utf-8"), expected_content
        )

    def test_update_package_provided_file_dry_run(self):
        """Test updating a package.provided file with dry_run=True."""
        temp_file = self.make_tempdir() / "package.provided"
        original_content = textwrap.dedent(
            """
            # Comment
            dev-lang/rust-1.78.0
            dev-lang/rust-1.79.0
            dev-lang/rust-host-1.78.0
            dev-lang/rust-host-1.79.0
            other-package/some-package-1.2.3
            """
        )
        temp_file.write_text(original_content, encoding="utf-8")

        version = auto_update_package_provided.rust_uprev.RustVersion.parse(
            "1.79.0"
        )
        changed = auto_update_package_provided.update_package_provided_file(
            temp_file, version, dry_run=True
        )

        self.assertTrue(changed)
        self.assertEqual(
            temp_file.read_text(encoding="utf-8"), original_content
        )

    def test_update_package_provided_file_keeps_newer_versions(self):
        """Test that we don't remove newer versions of rust."""
        temp_file = self.make_tempdir() / "package.provided"
        temp_file.write_text(
            textwrap.dedent(
                """
                dev-lang/rust-1.78.0
                dev-lang/rust-1.79.0
                dev-lang/rust-1.80.0
                dev-lang/rust-host-1.78.0
                dev-lang/rust-host-1.79.0
                dev-lang/rust-host-1.80.0
                """
            ),
            encoding="utf-8",
        )
        expected_content = textwrap.dedent(
            """
            dev-lang/rust-1.79.0
            dev-lang/rust-1.80.0
            dev-lang/rust-host-1.79.0
            dev-lang/rust-host-1.80.0
            """
        )

        version = auto_update_package_provided.rust_uprev.RustVersion.parse(
            "1.79.0"
        )
        changed = auto_update_package_provided.update_package_provided_file(
            temp_file, version, dry_run=False
        )

        self.assertTrue(changed)
        self.assertEqual(
            temp_file.read_text(encoding="utf-8"), expected_content
        )

    def test_update_package_provided_file_no_changes(self):
        """Test updating a package.provided file where no changes are needed."""
        temp_file = self.make_tempdir() / "package.provided"
        original_content = textwrap.dedent(
            """
            dev-lang/rust-1.79.0
            dev-lang/rust-host-1.79.0
            """
        )
        temp_file.write_text(original_content, encoding="utf-8")

        version = auto_update_package_provided.rust_uprev.RustVersion.parse(
            "1.79.0"
        )
        changed = auto_update_package_provided.update_package_provided_file(
            temp_file, version, dry_run=False
        )

        self.assertFalse(changed)
        self.assertEqual(
            temp_file.read_text(encoding="utf-8"), original_content
        )
