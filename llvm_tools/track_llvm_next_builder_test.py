# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for track_llvm_next_builder.py."""

import argparse
import unittest

from llvm_tools import bb_add
from llvm_tools import track_llvm_next_builder


class TestTrackLlvmNextBuilder(unittest.TestCase):
    """Tests for track_llvm_next_builder."""

    def test_resolve_builder_name_shorthand(self) -> None:
        self.assertEqual(
            track_llvm_next_builder.resolve_builder_name("asan"),
            "chromeos/staging/staging-amd64-generic-asan",
        )
        self.assertEqual(
            track_llvm_next_builder.resolve_builder_name("msan-fuzzer"),
            "chromeos/staging/staging-amd64-generic-msan-fuzzer",
        )
        self.assertEqual(
            track_llvm_next_builder.resolve_builder_name("ubsan"),
            "chromeos/staging/staging-amd64-generic-ubsan",
        )
        self.assertEqual(
            track_llvm_next_builder.resolve_builder_name("sdk"),
            "chromeos/staging/staging-build-chromiumos-sdk",
        )

    def test_resolve_builder_name_full_name(self) -> None:
        for full_builder in bb_add.DEFAULT_LLVM_NEXT_BUILDERS:
            with self.subTest(builder=full_builder):
                self.assertEqual(
                    track_llvm_next_builder.resolve_builder_name(full_builder),
                    full_builder,
                )

    def test_resolve_builder_name_invalid(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            track_llvm_next_builder.resolve_builder_name("non-existent-builder")
