# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for clean_up_old_llvm_patches"""

from llvm_tools import clean_up_old_llvm_patches
from llvm_tools import test_helpers


ANDROID_VERSION_PY_EXAMPLE = """
def get_svn_revision():
    return "r654321"
"""


class Test(test_helpers.TempDirTestCase):
    """Tests for clean_up_old_llvm_patches"""

    def test_android_version_autodetection(self):
        android_root = self.make_tempdir()
        android_version_py = (
            android_root
            / "toolchain"
            / "llvm_android"
            / "src"
            / "llvm_android"
            / "android_version.py"
        )
        android_version_py.parent.mkdir(parents=True)
        android_version_py.write_text(
            ANDROID_VERSION_PY_EXAMPLE, encoding="utf-8"
        )

        self.assertEqual(
            clean_up_old_llvm_patches.find_android_llvm_version(android_root),
            654321,
        )

    def test_chromeos_version_autodetection(self):
        chromiumos_overlay = self.make_tempdir()
        llvm = chromiumos_overlay / "sys-devel" / "llvm"
        llvm.mkdir(parents=True)

        file_names = (
            "Manifest",
            "llvm-12.0-r1.ebuild",
            "llvm-18.0_pre123456-r90.ebuild",
            "llvm-9999.ebuild",
        )
        for f in file_names:
            (llvm / f).touch()

        self.assertEqual(
            clean_up_old_llvm_patches.find_chromeos_llvm_version(
                chromiumos_overlay
            ),
            123456,
        )
