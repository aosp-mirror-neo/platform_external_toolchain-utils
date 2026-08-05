# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for chroot_toolchain.py."""

from unittest import mock

from llvm_tools import chroot_toolchain
from llvm_tools import test_helpers


class TestChrootToolchain(test_helpers.TempDirTestCase):
    """Tests for chroot_toolchain.py."""

    @mock.patch.object(
        chroot_toolchain.Path, "is_dir", return_value=True, autospec=True
    )
    def test_raise_if_board_not_set_up_exists(
        self, _mock_is_dir: mock.MagicMock
    ) -> None:
        # Should not raise
        chroot_toolchain.raise_if_board_not_set_up("myboard")

    @mock.patch.object(
        chroot_toolchain.Path, "is_dir", return_value=False, autospec=True
    )
    def test_raise_if_board_not_set_up_does_not_exist(
        self, _mock_is_dir: mock.MagicMock
    ) -> None:
        with self.assertRaises(ValueError):
            chroot_toolchain.raise_if_board_not_set_up("myboard")

    @mock.patch.object(chroot_toolchain.subprocess, "run", autospec=True)
    def test_clean_up_old_binpkgs(self, mock_run: mock.MagicMock) -> None:
        pkg_root = self.make_tempdir()
        category_dir = pkg_root / "sys-devel"
        category_dir.mkdir()

        (category_dir / "llvm-1.tbz2").touch()
        (category_dir / "llvm-2.tbz2").touch()
        (category_dir / "other-1.tbz2").touch()

        chroot_toolchain.clean_up_old_binpkgs(["sys-devel/llvm"], pkg_root)

        self.assertEqual(mock_run.call_count, 1)
        args, _ = mock_run.call_args
        cmd = args[0]
        self.assertEqual(cmd[0], "sudo")
        self.assertEqual(cmd[1], "rm")
        self.assertEqual(cmd[2], "-f")
        # Order of files might vary, so check set
        self.assertEqual(
            set(cmd[3:]),
            {
                str(category_dir / "llvm-1.tbz2"),
                str(category_dir / "llvm-2.tbz2"),
            },
        )

    @mock.patch.object(chroot_toolchain.subprocess, "run", autospec=True)
    def test_clean_up_old_binpkgs_no_matches(
        self, mock_run: mock.MagicMock
    ) -> None:
        pkg_root = self.make_tempdir()
        category_dir = pkg_root / "sys-devel"
        category_dir.mkdir()

        (category_dir / "other-1.tbz2").touch()

        chroot_toolchain.clean_up_old_binpkgs(["sys-devel/llvm"], pkg_root)

        # Should not call rm if no files to delete
        self.assertEqual(mock_run.call_count, 0)
