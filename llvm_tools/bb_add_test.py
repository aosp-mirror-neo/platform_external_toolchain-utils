# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for bb_add.py."""

from typing import Iterable
import unittest

from llvm_tools import bb_add
from llvm_tools import cros_cls
from llvm_tools import llvm_next


_ARBITRARY_BOTS = ["chromeos/cq/amd64-generic-cq"]


class Test(unittest.TestCase):
    """Tests for bb_add.py."""

    def set_llvm_next_cls(self, cls: Iterable[cros_cls.ChangeListURL]):
        old_cls = llvm_next.LLVM_NEXT_TESTING_CLS
        llvm_next.LLVM_NEXT_TESTING_CLS = cls

        def restore_cls():
            llvm_next.LLVM_NEXT_TESTING_CLS = old_cls

        self.addCleanup(restore_cls)

    def test_generate_bb_add_raises_if_no_llvm_next_cls(self):
        self.set_llvm_next_cls(())
        with self.assertRaisesRegex(
            ValueError, "^llvm-next testing requested.*"
        ):
            bb_add.generate_bb_add_command(
                use_llvm_next=True,
                extra_cls=(),
                bots=_ARBITRARY_BOTS,
                tags=(),
            )

    def test_generate_bb_add_adds_llvm_next_cls(self):
        self.set_llvm_next_cls((cros_cls.ChangeListURL(123, 1),))
        cmd = bb_add.generate_bb_add_command(
            use_llvm_next=True,
            extra_cls=(),
            bots=_ARBITRARY_BOTS,
            tags=(),
        )
        self.assertEqual(
            cmd, ["bb", "add", "-cl", "crrev.com/c/123/1"] + _ARBITRARY_BOTS
        )

    def test_generate_bb_add_adds_extra_cls(self):
        cmd = bb_add.generate_bb_add_command(
            use_llvm_next=False,
            extra_cls=(
                cros_cls.ChangeListURL(123, 1),
                cros_cls.ChangeListURL(126),
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

    def test_use_of_tags(self):
        cmd = bb_add.generate_bb_add_command(
            use_llvm_next=False,
            extra_cls=(cros_cls.ChangeListURL(126),),
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
