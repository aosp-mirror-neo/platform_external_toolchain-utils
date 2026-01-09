# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for file_new_warning_exemption_bugs."""

from pathlib import Path
import unittest

from android_tools import file_new_warning_exemption_bugs


class GetGitRepoRootTest(unittest.TestCase):
    """Tests for get_git_repo_root."""

    def test_finds_exact_match(self) -> None:
        repos = {Path("a"), Path("b"), Path("c")}
        self.assertEqual(
            file_new_warning_exemption_bugs.get_git_repo_root(
                repos, "//b:target"
            ),
            Path("b"),
        )

    def test_finds_parent_match(self) -> None:
        repos = {Path("a"), Path("b"), Path("c")}
        self.assertEqual(
            file_new_warning_exemption_bugs.get_git_repo_root(
                repos, "//b/subdir:target"
            ),
            Path("b"),
        )

    def test_finds_nested_repo(self) -> None:
        # "b/nested" should be picked over "b" for targets inside "b/nested"
        repos = {Path("a"), Path("b"), Path("b/nested"), Path("c")}

        self.assertEqual(
            file_new_warning_exemption_bugs.get_git_repo_root(
                repos, "//b/nested:target"
            ),
            Path("b/nested"),
        )
        self.assertEqual(
            file_new_warning_exemption_bugs.get_git_repo_root(
                repos, "//b/nested/deep:target"
            ),
            Path("b/nested"),
        )
        self.assertEqual(
            file_new_warning_exemption_bugs.get_git_repo_root(
                repos, "//b/other:target"
            ),
            Path("b"),
        )
        self.assertEqual(
            file_new_warning_exemption_bugs.get_git_repo_root(
                repos, "//b:target"
            ),
            Path("b"),
        )

    def test_raises_if_not_found(self) -> None:
        repos = {Path("a"), Path("b")}
        # "c" is not in repos, and "." is not in repos. Should raise ValueError.
        with self.assertRaises(ValueError):
            file_new_warning_exemption_bugs.get_git_repo_root(
                repos, "//c:target"
            )
