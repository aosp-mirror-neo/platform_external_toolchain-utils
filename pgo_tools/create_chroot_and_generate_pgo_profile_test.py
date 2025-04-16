#!/usr/bin/env python3
# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for generate_llvm_next_pgo."""

from pathlib import Path
import shutil
import tempfile
import unittest

# This script's name makes lines exceed 80 chars if it's not imported `as`
# something shorter.
from pgo_tools import create_chroot_and_generate_pgo_profile as create_chroot_etc


EXAMPLE_SDK_VERSION_CONF_FILE = r"""
# Copyright 2022 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# The last version of the sdk that we built & tested.
SDK_LATEST_VERSION="2024.04.22.140014"

# How to find the standalone toolchains from the above sdk.
TC_PATH="2024/04/%(target)s-2024.04.22.140014.tar.xz"

# Frozen version of SDK used for bootstrapping.
# If unset, SDK_LATEST_VERSION will be used for bootstrapping.
BOOTSTRAP_FROZEN_VERSION="2024.03.12.020106"

# The Google Storage bucket containing the SDK tarball and toolchains.
# If empty, Chromite will assume a default value, likely "chromiumos-sdk".
SDK_BUCKET=""
"""


class Test(unittest.TestCase):
    """Tests for generate_llvm_next_pgo."""

    def make_tempdir(self) -> Path:
        tempdir = Path(tempfile.mkdtemp(prefix="generate_llvm_next_pgo_test_"))
        self.addCleanup(lambda: shutil.rmtree(tempdir))
        return tempdir

    def test_sdk_version_detection_works(self):
        repo_root = self.make_tempdir()
        sdk_version_conf = repo_root / create_chroot_etc.SDK_VERSION_CONF_SUBDIR
        sdk_version_conf.parent.mkdir(parents=True)
        sdk_version_conf.write_text(
            EXAMPLE_SDK_VERSION_CONF_FILE, encoding="utf-8"
        )
        self.assertEqual(
            create_chroot_etc.detect_bootstrap_sdk_version(repo_root),
            "2024.03.12.020106",
        )

    def test_path_translation_works(self):
        repo_root = Path("/some/repo")
        chroot_info = create_chroot_etc.ChrootInfo(
            chroot_name="my-chroot",
            out_dir_name="my-out",
            sdk_version="123",
        )
        self.assertEqual(
            create_chroot_etc.translate_chroot_path_to_out_of_chroot(
                repo_root, "/tmp/file/path", chroot_info
            ),
            repo_root / "my-out" / "tmp/file/path",
        )
