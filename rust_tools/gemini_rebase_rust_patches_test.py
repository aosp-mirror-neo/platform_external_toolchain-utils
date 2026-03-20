# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for gemini_rebase_rust_patches.py"""

import os

from llvm_tools import test_helpers
from rust_tools import gemini_rebase_rust_patches


class GeminiRebaseRustPatchesTest(test_helpers.TempDirTestCase):
    """Tests for gemini_rebase_rust_patches."""

    def test_create_temp_bin_dir(self) -> None:
        with gemini_rebase_rust_patches.create_temp_bin_dir() as temp_bin:
            self.assertTrue(temp_bin.is_dir())
            script_path = (
                temp_bin / gemini_rebase_rust_patches.TRY_PATCH_SCRIPT_NAME
            )
            self.assertTrue(script_path.exists())
            self.assertTrue(os.access(script_path, os.X_OK))
            content = script_path.read_text(encoding="utf-8")
            self.assertEqual(
                content, gemini_rebase_rust_patches.TRY_PATCH_SCRIPT_CONTENTS
            )

    def test_generate_gemini_prompt(self) -> None:
        cros_checkout = self.make_tempdir()

        # Create necessary files
        host_path = (
            cros_checkout / gemini_rebase_rust_patches.RUST_HOST_9999_PATH
        )
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.touch()

        eclass_path = (
            cros_checkout / gemini_rebase_rust_patches.RUST_ECLASS_PATH
        )
        eclass_path.parent.mkdir(parents=True, exist_ok=True)
        eclass_path.touch()

        patches_path = (
            cros_checkout / gemini_rebase_rust_patches.RUST_HOST_PATCHES_PATH
        )
        patches_path.mkdir(parents=True, exist_ok=True)

        work_dir = cros_checkout / gemini_rebase_rust_patches.RUST_PORTAGE_PATH
        work_dir.mkdir(parents=True, exist_ok=True)

        # Create the source dir
        src_dir = work_dir / "rustc-1.2.3-src"
        src_dir.mkdir()

        prompt = gemini_rebase_rust_patches.generate_gemini_prompt(
            cros_checkout
        )

        # Verify relative path usage
        expected_rel_path = (
            "out/sdk/tmp/portage/dev-lang/rust-host-9999/work/rustc-1.2.3-src"
        )
        self.assertIn(expected_rel_path, prompt)
        self.assertIn(
            f"Run `git init` in `{expected_rel_path}`",
            prompt,
        )
