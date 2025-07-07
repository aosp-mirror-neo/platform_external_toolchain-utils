# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for git_utils."""

import subprocess
import textwrap
from unittest import mock

from cros_utils import git_utils
from llvm_tools import test_helpers


# pylint: disable=protected-access

EXAMPLE_GIT_SHA = "d46d9c1a23118e3943f43fe2dfc9f9c9c0b4aefe"

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


class Test(test_helpers.TempDirTestCase):
    """Tests for git_utils."""

    def test_is_full_git_sha_success_cases(self):
        shas = ("a" * 40, EXAMPLE_GIT_SHA)
        for s in shas:
            self.assertTrue(git_utils.is_full_git_sha(s), s)

    def test_is_full_git_sha_failure_cases(self):
        shas = (
            "",
            "A" * 40,
            "g" * 40,
            EXAMPLE_GIT_SHA[1:],
            EXAMPLE_GIT_SHA + "a",
        )
        for s in shas:
            self.assertFalse(git_utils.is_full_git_sha(s), s)

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

    def test_parse_message_metadata(self):
        """Test we can parse commit metadata."""

        message_lines = [
            "Some subject line here",
            "",
            "Here is my commit message!",
            "",
            "BUG=None",
            "TEST=None",
            "",
            "patch.cherry: true",
            "patch.version_range.from: 1245",
            "patch.version_range.until: null",
            "Commit-Id: abcdef1234567890",
        ]
        parsed = git_utils.parse_message_metadata(message_lines)
        self.assertEqual(parsed["patch.cherry"], "true")
        self.assertEqual(parsed["patch.version_range.from"], "1245")
        self.assertEqual(parsed["patch.version_range.until"], "null")
        self.assertEqual(parsed.get("BUG"), None)

    def test_channel_parsing(self):
        with self.assertRaisesRegex(ValueError, "No such channel.*"):
            git_utils.Channel.parse("not a channel")

        # Ensure these round-trip.
        for channel in git_utils.Channel:
            self.assertEqual(channel, git_utils.Channel.parse(channel.value))

    @mock.patch.object(subprocess, "run")
    def test_branch_autodetection(self, subprocess_run):
        subprocess_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=textwrap.dedent(
                """
                cros/not-a-release-branch
                cros/release-R121-15699.B
                cros/release-R122-15753.B
                cros/release-R123-15786.B
                cros/also-not-a-release-branch
                m/main
                """
            ),
        )

        branch_dict = git_utils.autodetect_cros_channels(
            git_repo=self.make_tempdir()
        )

        self.assertEqual(
            branch_dict,
            {
                git_utils.Channel.CANARY: git_utils.ChannelBranch(
                    remote="cros",
                    release_number=124,
                    branch_name="main",
                ),
                git_utils.Channel.BETA: git_utils.ChannelBranch(
                    remote="cros",
                    release_number=123,
                    branch_name="release-R123-15786.B",
                ),
                git_utils.Channel.STABLE: git_utils.ChannelBranch(
                    remote="cros",
                    release_number=122,
                    branch_name="release-R122-15753.B",
                ),
            },
        )


class ShowFileAtRevTest(test_helpers.TempDirTestCase):
    """Class for testing the show-file-at-rev functionality.

    This is tested against git since it has heuristics matching against git
    output in error cases.
    """

    def test_show_file_at_rev_works(self):
        temp_dir = self.make_tempdir()
        subprocess.run(
            ["git", "init"],
            check=True,
            cwd=temp_dir,
            stdin=subprocess.DEVNULL,
        )
        (temp_dir / "foo").write_text("old text")
        git_utils.commit_all_changes(temp_dir, message="commit 1")
        (temp_dir / "foo").write_text("new text")
        git_utils.commit_all_changes(temp_dir, message="commit 2")

        # Test multiple cases here to avoid setting up multiple git dirs on
        # every invocation of this test. They're reasonably self-contained
        # anyway.
        self.assertEqual(
            git_utils.maybe_show_file_at_commit(temp_dir, "HEAD", "foo"),
            "new text",
        )
        self.assertEqual(
            git_utils.maybe_show_file_at_commit(temp_dir, "HEAD~", "foo"),
            "old text",
        )

        self.assertIsNone(
            git_utils.maybe_show_file_at_commit(temp_dir, "HEAD", "bar")
        )

    def test_show_dir_at_rev_works(self):
        temp_dir = self.make_tempdir()
        subprocess.run(
            ["git", "init"],
            check=True,
            cwd=temp_dir,
            stdin=subprocess.DEVNULL,
        )
        (temp_dir / "file").write_text("foo")
        git_utils.commit_all_changes(temp_dir, message="commit 1")
        (temp_dir / "dir").mkdir()
        (temp_dir / "dir" / "subfile1").touch()
        git_utils.commit_all_changes(temp_dir, message="commit 2")

        (temp_dir / "dir" / "subfile2").touch()
        (temp_dir / "dir" / "subdir").mkdir()
        (temp_dir / "dir" / "subdir" / "subfile3").touch()
        git_utils.commit_all_changes(temp_dir, message="commit 3")

        # Test multiple cases here to avoid setting up multiple git dirs on
        # every invocation of this test. They're reasonably self-contained
        # anyway.
        self.assertIsNone(
            git_utils.maybe_list_dir_contents_at_commit(
                temp_dir, "HEAD~~", "dir"
            ),
        )

        self.assertEqual(
            git_utils.maybe_list_dir_contents_at_commit(
                temp_dir, "HEAD~", "dir"
            ),
            ["subfile1"],
        )

        self.assertEqual(
            sorted(
                git_utils.maybe_list_dir_contents_at_commit(
                    temp_dir, "HEAD", "dir"
                )
            ),
            ["subdir/", "subfile1", "subfile2"],
        )

        with self.assertRaisesRegex(ValueError, ".*isn't a directory$"):
            git_utils.maybe_list_dir_contents_at_commit(
                temp_dir, "HEAD", "file"
            )


class FormatPatchTest(test_helpers.TempDirTestCase):
    """Class for testing format_patch.

    This is separated because it derives from TempDirTestCase,
    which gives us nice temp directories.
    """

    def setUp(self):
        """Set up the tests."""

        # This cleans up automatically. No tearDown needed.
        self.temp_dir = self.make_tempdir()
        subprocess.run(
            ["git", "init"],
            check=True,
            cwd=self.temp_dir,
            stdin=subprocess.DEVNULL,
        )
        self.foo_contents = "initial commit text"
        (self.temp_dir / "foo").write_text(self.foo_contents, encoding="utf-8")
        subject = "Initial commit"
        git_utils.commit_all_changes(self.temp_dir, message=subject)

        self.foo_contents = "here is special test text :)"
        (self.temp_dir / "foo").write_text(self.foo_contents, encoding="utf-8")
        subject = "Second commit"
        git_utils.commit_all_changes(self.temp_dir, message=subject)

    def test_format_patch(self):
        """Test that we can format patches correctly."""

        formatted_patch = git_utils.format_patch(self.temp_dir, "HEAD")
        self.assertIn(
            "Subject: [PATCH] Second commit",
            formatted_patch,
        )
        self.assertIn(
            self.foo_contents,
            formatted_patch,
        )
