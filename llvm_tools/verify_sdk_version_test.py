#!/usr/bin/env python3

# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for verify_sdk_version."""

from pathlib import Path
import textwrap
import unittest
from unittest import mock

from cros_utils import gs
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

    @mock.patch.object(gs, "ls", autospec=True)
    def test_verify_toolchain_urls_all_exist(
        self, mock_ls: mock.MagicMock
    ) -> None:
        mock_ls.side_effect = lambda url, *_: [
            gs.GsEntry(last_modified=None, gs_path=url)
        ]
        tc_path = "2026/08/%(target)s-2026.08.04.1.tar.xz"
        targets = ("x86_64-cros-linux-gnu", "aarch64-cros-linux-gnu")
        errors = verify_sdk_version.verify_toolchain_urls(tc_path, targets)
        self.assertEqual(errors, [])
        self.assertEqual(mock_ls.call_count, 2)

    @mock.patch.object(gs, "ls", autospec=True)
    def test_verify_toolchain_urls_some_missing(
        self, mock_ls: mock.MagicMock
    ) -> None:
        mock_ls.side_effect = lambda url, *_: (
            [gs.GsEntry(last_modified=None, gs_path=url)]
            if "x86_64" in url
            else []
        )
        tc_path = "2026/08/%(target)s-2026.08.04.1.tar.xz"
        targets = ("x86_64-cros-linux-gnu", "aarch64-cros-linux-gnu")
        errors = verify_sdk_version.verify_toolchain_urls(tc_path, targets)
        self.assertEqual(len(errors), 1)
        self.assertIn("aarch64-cros-linux-gnu", errors[0])
