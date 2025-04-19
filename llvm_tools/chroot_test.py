# Copyright 2020 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for chroot.py."""

from llvm_tools import chroot
from llvm_tools import test_helpers


class Test(test_helpers.TempDirTestCase):
    """Tests for chroot.py."""

    def test_chromeos_root_finding_works(self):
        root = self.make_tempdir()
        (root / ".repo").mkdir()
        self.assertEqual(chroot.FindChromeOSRootAbove(root), root)

        subdir = root / "foo" / "bar" / "baz"
        subdir.mkdir(parents=True)
        self.assertEqual(chroot.FindChromeOSRootAbove(subdir), root)

    def test_chromeos_root_finding_raises_in_trivial_case(self):
        root = self.make_tempdir()
        subdir = root / "foo" / "bar" / "baz"
        subdir.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "not in a repo checkout$"):
            chroot.FindChromeOSRootAbove(subdir)
