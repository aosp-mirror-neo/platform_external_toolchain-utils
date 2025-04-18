# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for upload_llvm_testing_helper_cl"""

from llvm_tools import test_helpers
from llvm_tools import upload_llvm_testing_helper_cl


class Test(test_helpers.TempDirTestCase):
    """Tests for upload_llvm_testing_helper_cl"""

    def test_force_rebuild_marker_addition(self):
        chromiumos_overlay = self.make_tempdir()
        llvm_filesdir = chromiumos_overlay / "sys-devel" / "llvm" / "files"
        llvm_filesdir.mkdir(parents=True)
        upload_llvm_testing_helper_cl.add_force_rebuild_marker(
            chromiumos_overlay
        )
        self.assertTrue(
            (llvm_filesdir / "force_rebuild").exists(),
            f"Missing force_rebuild marker in {llvm_filesdir}",
        )

    def test_use_force_block_addition(self):
        chromiumos_overlay = self.make_tempdir()
        use_force_file = chromiumos_overlay / "profiles" / "base" / "use.force"
        use_force_file.parent.mkdir(parents=True)
        use_force_file.write_text("# Whee", encoding="utf-8")

        upload_llvm_testing_helper_cl.add_use_force_block(chromiumos_overlay)
        new_contents = use_force_file.read_text(encoding="utf-8")

        self.assertIn("# Whee\n", new_contents)
        self.assertIn(
            upload_llvm_testing_helper_cl.USE_FORCE_BLOCK, new_contents
        )

    def test_warning_disable_block_addition(self):
        chromiumos_overlay = self.make_tempdir()
        profile_bashrc = (
            chromiumos_overlay / "profiles" / "base" / "profile.bashrc"
        )
        profile_bashrc.parent.mkdir(parents=True)
        profile_bashrc.write_text("# Whee", encoding="utf-8")

        upload_llvm_testing_helper_cl.add_disable_warnings_block(
            chromiumos_overlay
        )
        new_contents = profile_bashrc.read_text(encoding="utf-8")

        self.assertIn("# Whee\n", new_contents)
        self.assertIn(
            upload_llvm_testing_helper_cl.DISABLE_WARNINGS_BLOCK, new_contents
        )
