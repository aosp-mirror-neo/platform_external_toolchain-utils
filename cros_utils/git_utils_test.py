#!/usr/bin/env python3
# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for git_utils."""

import unittest

from cros_utils import git_utils


# pylint: disable=protected-access

GERRIT_OUTPUT_WITH_ONE_CL = r"""
Enumerating objects: 4, done.
Counting objects: 100% (4/4), done.
Delta compression using up to 128 threads
Compressing objects: 100% (2/2), done.
Writing objects: 100% (3/3), 320 bytes | 106.00 KiB/s, done.
Total 3 (delta 1), reused 1 (delta 0), pack-reused 0 (from 0)
remote: Resolving deltas: 100% (1/1)
remote: Processing changes: refs: 1, new: 1, done
remote:
remote: SUCCESS
remote:
remote:   https://chromium-review.googlesource.com/c/chromiumos/third_party/toolchain-utils/+/5375204 DO NOT COMMIT [WIP] [NEW]
remote:
To https://chromium.googlesource.com/chromiumos/third_party/toolchain-utils
 * [new reference]     HEAD -> refs/for/main
"""

GERRIT_OUTPUT_WITH_TWO_CLS = r"""
Enumerating objects: 4, done.
Counting objects: 100% (4/4), done.
Delta compression using up to 128 threads
Compressing objects: 100% (2/2), done.
Writing objects: 100% (3/3), 320 bytes | 106.00 KiB/s, done.
Total 3 (delta 1), reused 1 (delta 0), pack-reused 0 (from 0)
remote: Resolving deltas: 100% (1/1)
remote: Processing changes: refs: 1, new: 1, done
remote:
remote: SUCCESS
remote:
remote:   https://chromium-review.googlesource.com/c/chromiumos/third_party/toolchain-utils/+/5375204 DO NOT COMMIT [WIP] [NEW]
remote:   https://chromium-review.googlesource.com/c/chromiumos/third_party/toolchain-utils/+/5375205 DO NOT COMMIT [WIP] [NEW]
remote:
To https://chromium.googlesource.com/chromiumos/third_party/toolchain-utils
 * [new reference]     HEAD -> refs/for/main
"""


GERRIT_OUTPUT_WITH_INTERNAL_CL = r"""
Upload project manifest-internal/ to remote branch refs/heads/main:
  branch DO-NOT-COMMIT ( 1 commit, Tue Apr 16 08:51:25 2024 -0600):
         456aadd0 DO NOT COMMIT
to https://chrome-internal-review.googlesource.com (y/N)? <--yes>
Enumerating objects: 5, done.
Counting objects: 100% (5/5), done.
Delta compression using up to 128 threads
Compressing objects: 100% (3/3), done.
Writing objects: 100% (3/3), 334 bytes | 334.00 KiB/s, done.
Total 3 (delta 2), reused 0 (delta 0), pack-reused 0 (from 0)
remote: Resolving deltas: 100% (2/2)
remote: Waiting for private key checker: 1/1 objects left
remote: Processing changes: refs: 1, new: 1, done
remote:
remote: SUCCESS
remote:
remote:   https://chrome-internal-review.googlesource.com/c/chromeos/manifest-internal/+/7190037 DO NOT COMMIT [NEW]
remote:
To https://chrome-internal-review.googlesource.com/chromeos/manifest-internal
 * [new reference]         DO-NOT-COMMIT -> refs/for/main

----------------------------------------------------------------------
[OK    ] manifest-internal/ DO-NOT-COMMIT
"""


class Test(unittest.TestCase):
    """Tests for git_utils."""

    def test_cl_parsing_complains_if_no_output(self):
        with self.assertRaisesRegex(ValueError, ".*; found 0"):
            git_utils._parse_cls_from_upload_output("")

    def test_cl_parsing_works_with_one_cl(self):
        self.assertEqual(
            git_utils._parse_cls_from_upload_output(GERRIT_OUTPUT_WITH_ONE_CL),
            [5375204],
        )

    def test_cl_parsing_works_with_two_cls(self):
        self.assertEqual(
            git_utils._parse_cls_from_upload_output(GERRIT_OUTPUT_WITH_TWO_CLS),
            [5375204, 5375205],
        )

    def test_cl_parsing_works_with_internal_cl(self):
        self.assertEqual(
            git_utils._parse_cls_from_upload_output(
                GERRIT_OUTPUT_WITH_INTERNAL_CL
            ),
            [7190037],
        )


if __name__ == "__main__":
    unittest.main()
