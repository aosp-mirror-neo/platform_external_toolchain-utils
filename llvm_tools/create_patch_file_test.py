# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for create_patch_file."""

from pathlib import Path
from typing import Optional
import unittest

from llvm_tools import create_patch_file
from llvm_tools import git_llvm_rev
from llvm_tools import patch_utils


COMMIT_FIXTURE_1 = """Commit Fixture 1

BUG=None
TEST=CQ

Change-Id: I01234567abcedf
---
Change-Id: I01234567abcedf
"""

COMMIT_FIXTURE_1_CLEAN = """Commit Fixture 1

BUG=None
TEST=CQ

---
Change-Id: I01234567abcedf
"""

COMMIT_FIXTURE_2 = """
From 2939fec6d34e57ccfc1ee66d4a7885d88db82d16 Mon Sep 17 00:00:00 2001
From: Jordan R Abrahams-Whitehead <ajordanr@google.com>
Date: Fri, 31 May 2024 20:53:22 +0000
Subject: [PATCH] llvm-project: ChromeOS Base Commit

This is the LLVM ChromeOS Base Commit.

This commit marks the start of the ChromeOS patch branch. It introduces
the OWNERS file, and sets up the 'cros' directory for future use.

Functional patches for the ChromeOS LLVM Toolchain land after this
commit. This commit does not change how LLVM operates. The parent
commit to this change determines the LLVM synthetic revision.

BUG=b:343568613
TEST=CQ

Change-Id: I5cc72b7cfd9ac1c47f6acbf29f5e14314d75a0c6
---
 OWNERS         | 3 +++
 cros/README.md | 7 +++++++
 2 files changed, 10 insertions(+)
 create mode 100644 OWNERS
 create mode 100644 cros/README.md

diff --git a/OWNERS b/OWNERS
new file mode 100644
index 000000000000..e692fc288f75
--- /dev/null
+++ b/OWNERS
@@ -0,0 +1,3 @@
+set noparent
+
+include chromiumos/third_party/toolchain-utils:/OWNERS.toolchain
diff --git a/cros/README.md b/cros/README.md
new file mode 100644
index 000000000000..21aeeddbe44f
--- /dev/null
+++ b/cros/README.md
@@ -0,0 +1,7 @@
+# CrOS Directory
+
+This directory is used to store arbitrary changes for the ChromeOS Toolchain
+team. Files in this directory are never meant to be upstreamed, and only
+exist for local modification.
+
+See src/third_party/toolchain-utils to see how this directory is configured.
--
2.45.2.627.g7a2c4fd464-goog
"""

UNIVERSAL_DIFF_FIXTURE = """Disable Cast Assertion

Change-Id: Iabcedef123456789

diff --git a/llvm/include/llvm/Support/Casting.h b/llvm/include/llvm/Support/Casting.h
index 8a2fa94f9cca..e5326c277d05 100644
--- a/llvm/include/llvm/Support/Casting.h
+++ b/llvm/include/llvm/Support/Casting.h

Change-Id: Iabcedef123456789
"""


class TestCreatePatchFile(unittest.TestCase):
    """Test harness for create_patch_file."""

    @staticmethod
    def _make_patch_entry(
        from_: Optional[int], until: Optional[int], title: str = "Some title"
    ):
        return patch_utils.PatchEntry(
            workdir=Path(),
            metadata={
                "info": [],
                "title": title,
            },
            platforms=["some platform"],
            rel_patch_path="a/path/to/a/patch.patch",
            version_range={"from": from_, "until": until},
        )

    def test_find_new_patches_normal(self):
        """Test that we only find newer patches applied to a given branch."""

        llvm_rev = git_llvm_rev.Rev(git_llvm_rev.MAIN_BRANCH, 1234)
        version_ranges = (
            (1, 2),
            (5, 100),
            (1234, 1235),
            (1, 1235),
            (None, None),
        )
        existing_patches = [
            self._make_patch_entry(from_, until)
            for from_, until in version_ranges
        ]
        branch_version_ranges = ((1, 1984), (1000, 9001))
        branch_combos = [
            create_patch_file.PatchCombo(
                self._make_patch_entry(from_, until),
                "[some contents]",
            )
            for from_, until in ((1234, 1235), (1, 1235), (None, None))
            + branch_version_ranges
        ]
        branch_combos.insert(
            0,
            create_patch_file.PatchCombo(
                self._make_patch_entry(None, None, "llvm-project: Base Commit"),
                "[PATCH] llvm-project: Base Commit",
            ),
        )
        branch_context = create_patch_file.BranchContext(
            branch_ref="some-branch-name",
            merge_base="abcdef",
            llvm_rev=llvm_rev,
            patch_entry_combos=branch_combos,
        )
        new_patch_combos = create_patch_file.find_new_patches(
            branch_context, existing_patches
        )
        self.assertEqual(
            new_patch_combos, branch_combos[-len(branch_version_ranges) :]
        )

    def test_filter_change_id_1(self):
        """Test filter_change_id."""
        filtered_fixture = create_patch_file.filter_change_id(COMMIT_FIXTURE_1)
        self.assertIn("Change-Id", COMMIT_FIXTURE_1)
        self.assertEqual(filtered_fixture, COMMIT_FIXTURE_1_CLEAN)

    def test_filter_change_id_2(self):
        """Test filter_change_id again."""
        fixture_line_count = COMMIT_FIXTURE_2.count("\n")
        filtered_fixture = create_patch_file.filter_change_id(COMMIT_FIXTURE_2)
        self.assertNotIn("Change-Id", filtered_fixture)
        self.assertEqual(fixture_line_count, filtered_fixture.count("\n") + 1)

    def test_filter_change_id_universal(self):
        """Test filter_change_id again, but for universal diffs."""
        fixture_line_count = UNIVERSAL_DIFF_FIXTURE.count("\n")
        filtered_fixture = create_patch_file.filter_change_id(
            UNIVERSAL_DIFF_FIXTURE
        )
        self.assertEqual(fixture_line_count, filtered_fixture.count("\n") + 1)
