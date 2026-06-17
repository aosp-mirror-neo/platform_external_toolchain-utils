# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for bb_add.py."""

from pathlib import Path
import unittest
from unittest import mock

from cros_utils import gerrit_utils
from llvm_tools import bb_add
from llvm_tools import cros_cls
from llvm_tools import llvm_next


_ARBITRARY_BOTS = ["chromeos/cq/amd64-generic-cq"]


class Test(unittest.TestCase):
    """Tests for bb_add.py."""

    def test_generate_bb_add_adds_extra_cls(self) -> None:
        cmd = bb_add.generate_bb_add_command(
            extra_cls=(
                gerrit_utils.ChangeListURL(123, 1),
                gerrit_utils.ChangeListURL(126),
            ),
            bots=_ARBITRARY_BOTS,
            tags=(),
        )
        self.assertEqual(
            cmd,
            [
                "bb",
                "add",
                "-cl",
                "crrev.com/c/123/1",
                "-cl",
                "crrev.com/c/126",
            ]
            + _ARBITRARY_BOTS,
        )

    def test_use_of_tags(self) -> None:
        cmd = bb_add.generate_bb_add_command(
            extra_cls=(gerrit_utils.ChangeListURL(126),),
            bots=_ARBITRARY_BOTS,
            tags=("custom-tag",),
        )
        self.assertEqual(
            cmd,
            [
                "bb",
                "add",
                "-cl",
                "crrev.com/c/126",
                "-t",
                "custom-tag",
            ]
            + _ARBITRARY_BOTS,
        )

    @mock.patch.object(cros_cls, "fetch_gerrit_deps_of_most_recent_patchset")
    @mock.patch.object(cros_cls, "fetch_current_toolchain_owners")
    def test_fetch_llvm_next_deps_or_exit_main_cl_is_trusted(
        self, mock_owners: mock.MagicMock, mock_fetch_deps: mock.MagicMock
    ) -> None:
        main_cl = gerrit_utils.ChangeListURL(cl_id=12345, patch_set=1)
        mock_fetch_deps.return_value = [
            cros_cls.GerritChange(url=main_cl, uploader="untrusted@user.com")
        ]
        mock_owners.return_value = ["owner@google.com"]

        result = bb_add.fetch_llvm_next_deps_or_exit(
            main_cl,
            chromeos_tree=Path("/fake/path"),
            untrusted_reject=False,
            untrusted_ignore=True,
        )

        self.assertEqual(result, [main_cl])

    @mock.patch.object(cros_cls, "fetch_gerrit_deps_of_most_recent_patchset")
    @mock.patch.object(cros_cls, "fetch_current_toolchain_owners")
    def test_fetch_llvm_next_deps_or_exit_trusted_uploader(
        self, mock_owners: mock.MagicMock, mock_fetch_deps: mock.MagicMock
    ) -> None:
        main_cl = gerrit_utils.ChangeListURL(cl_id=12345, patch_set=1)
        dep_cl = gerrit_utils.ChangeListURL(cl_id=67890, patch_set=1)
        mock_fetch_deps.return_value = [
            cros_cls.GerritChange(url=main_cl, uploader="untrusted@user.com"),
            cros_cls.GerritChange(url=dep_cl, uploader="trusted@uploader.com"),
        ]
        mock_owners.return_value = ["owner@google.com"]

        with mock.patch.object(
            llvm_next, "TRUSTED_UPLOADERS", ("trusted@uploader.com",)
        ):
            result = bb_add.fetch_llvm_next_deps_or_exit(
                main_cl,
                chromeos_tree=Path("/fake/path"),
                untrusted_reject=False,
                untrusted_ignore=False,
            )

        self.assertEqual(result, [main_cl, dep_cl])
