# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for llvm_next."""

import unittest

from llvm_tools import cros_cls
from llvm_tools import llvm_next


class Test(unittest.TestCase):
    """Tests for llvm_next."""

    def test_trusted_uploaders_disjoint_from_owners(self) -> None:
        owners = cros_cls.fetch_current_toolchain_owners()
        intersection = set(owners) & set(llvm_next.TRUSTED_UPLOADERS)
        self.assertEqual(
            intersection,
            set(),
            f"Users in both TRUSTED_UPLOADERS and OWNERS: {intersection}",
        )
