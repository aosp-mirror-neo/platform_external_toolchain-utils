#!/usr/bin/env python3

# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for verify_sdk_version."""

from pathlib import Path
import subprocess
import textwrap
import unittest
from unittest import mock

from llvm_tools import verify_sdk_version


class VerifySdkVersionTest(unittest.TestCase):
    """Test suite for verify_sdk_version."""

    def test_parse_sdk_version_conf_contents_success(self) -> None:
        content = textwrap.dedent(
            """\
            # Comment line
            SDK_LATEST_VERSION="2026.08.04.86576"
            TC_PATH="2026/08/%(target)s-2026.08.04.86576.tar.xz"
            SDK_BUCKET="chromiumos-sdk"
            """
        )
        tc_path = verify_sdk_version.parse_sdk_version_conf_contents(
            Path("sdk_version.conf"), content
        )
        self.assertEqual(tc_path, "2026/08/%(target)s-2026.08.04.86576.tar.xz")

    def test_parse_sdk_version_conf_contents_invalid_bucket(self) -> None:
        content = textwrap.dedent(
            """\
            SDK_LATEST_VERSION="2026.08.04.86576"
            TC_PATH="2026/08/%(target)s-2026.08.04.86576.tar.xz"
            SDK_BUCKET="custom-bucket"
            """
        )
        with self.assertRaisesRegex(ValueError, "Unexpected SDK_BUCKET"):
            verify_sdk_version.parse_sdk_version_conf_contents(
                Path("sdk_version.conf"), content
            )

    def test_parse_sdk_version_conf_contents_missing_tc_path(self) -> None:
        content = 'SDK_LATEST_VERSION="2026.08.04.86576"\n'
        with self.assertRaisesRegex(ValueError, "TC_PATH not found"):
            verify_sdk_version.parse_sdk_version_conf_contents(
                Path("sdk_version.conf"), content
            )

    def test_parse_sdk_version_conf_contents_invalid_tc_path(self) -> None:
        content = textwrap.dedent(
            """\
            SDK_LATEST_VERSION="2026.08.04.86576"
            TC_PATH="2026/08/invalid-path.tar.xz"
            """
        )
        with self.assertRaisesRegex(
            ValueError, "does not contain '%\\(target\\)s'"
        ):
            verify_sdk_version.parse_sdk_version_conf_contents(
                Path("sdk_version.conf"), content
            )

    @mock.patch.object(subprocess, "run", autospec=True)
    def test_gs_url_exists_true(self, mock_run: mock.MagicMock) -> None:
        mock_run.return_value = mock.MagicMock(returncode=0)
        self.assertTrue(
            verify_sdk_version.gs_url_exists("gs://bucket/path/file.tar.xz")
        )
        mock_run.assert_called_once_with(
            ("gsutil", "ls", "gs://bucket/path/file.tar.xz"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @mock.patch.object(subprocess, "run", autospec=True)
    def test_gs_url_exists_false(self, mock_run: mock.MagicMock) -> None:
        mock_run.return_value = mock.MagicMock(returncode=1)
        self.assertFalse(
            verify_sdk_version.gs_url_exists("gs://bucket/path/file.tar.xz")
        )

    @mock.patch.object(verify_sdk_version, "gs_url_exists", autospec=True)
    def test_verify_toolchain_urls_all_exist(
        self, mock_exists: mock.MagicMock
    ) -> None:
        mock_exists.return_value = True
        tc_path = "2026/08/%(target)s-2026.08.04.1.tar.xz"
        targets = ("x86_64-cros-linux-gnu", "aarch64-cros-linux-gnu")
        errors = verify_sdk_version.verify_toolchain_urls(tc_path, targets)
        self.assertEqual(errors, [])
        self.assertEqual(mock_exists.call_count, 2)

    @mock.patch.object(verify_sdk_version, "gs_url_exists", autospec=True)
    def test_verify_toolchain_urls_some_missing(
        self, mock_exists: mock.MagicMock
    ) -> None:
        mock_exists.side_effect = lambda url, **_: "x86_64" in url
        tc_path = "2026/08/%(target)s-2026.08.04.1.tar.xz"
        targets = ("x86_64-cros-linux-gnu", "aarch64-cros-linux-gnu")
        errors = verify_sdk_version.verify_toolchain_urls(tc_path, targets)
        self.assertEqual(len(errors), 1)
        self.assertIn("aarch64-cros-linux-gnu", errors[0])
