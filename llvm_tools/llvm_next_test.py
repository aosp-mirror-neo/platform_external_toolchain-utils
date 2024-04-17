#!/usr/bin/env python3
# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for llvm_next."""

import unittest

import llvm_next


class Test(unittest.TestCase):
    """Tests for llvm_next."""

    def test_all_cls_have_patchesets(self):
        cls = [llvm_next.DISABLE_WERROR_CL]
        cls += llvm_next.LLVM_NEXT_TESTING_CLS
        for cl in cls:
            self.assertIsNotNone(cl.patch_set, f"CL {cl} needs a patch-set")


if __name__ == "__main__":
    unittest.main()
