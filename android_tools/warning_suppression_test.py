# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for warning_suppression."""

from pathlib import Path

from android_tools import warning_suppression
from llvm_tools import test_helpers


class ConvertTargetToAndroidBpTest(test_helpers.TempDirTestCase):
    """Tests for convert_target_to_android_bp."""

    def test_well_formed_target(self) -> None:
        result = warning_suppression.convert_target_to_android_bp(
            "//bionic/libc:libc"
        )
        self.assertEqual(result, Path("bionic/libc/Android.bp"))

    def test_target_with_long_path(self) -> None:
        result = warning_suppression.convert_target_to_android_bp(
            "//system/core/long/path/to/target:some_target"
        )
        self.assertEqual(
            result,
            Path("system/core/long/path/to/target/Android.bp"),
        )

    def test_target_with_dots(self) -> None:
        result = warning_suppression.convert_target_to_android_bp(
            "//system/core/lib.so:lib.so"
        )
        self.assertEqual(result, Path("system/core/lib.so/Android.bp"))

    def test_malformed_target_no_colon(self) -> None:
        with self.assertRaises(ValueError):
            warning_suppression.convert_target_to_android_bp("//bionic/libc")

    def test_malformed_target_no_double_slash(self) -> None:
        with self.assertRaises(ValueError):
            warning_suppression.convert_target_to_android_bp("bionic/libc:libc")

    def test_empty_target(self) -> None:
        with self.assertRaises(ValueError):
            warning_suppression.convert_target_to_android_bp("")
