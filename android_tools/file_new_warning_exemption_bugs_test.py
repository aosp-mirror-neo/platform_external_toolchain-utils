# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for file_new_warning_exemption_bugs."""

from pathlib import Path
import unittest
from unittest import mock

from android_tools import file_new_warning_exemption_bugs
from android_tools import find_owners


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


class ProcessTargetsTest(unittest.TestCase):
    """Tests for process_targets."""

    def test_groups_targets(self) -> None:
        repos = {Path("a"), Path("b")}
        targets = ["//a:t1", "//b/sub:t2", "//a:t3"]

        result = file_new_warning_exemption_bugs.process_targets(repos, targets)

        self.assertEqual(
            result.targets_by_repo,
            {
                Path("a"): ["//a:t1", "//a:t3"],
                Path("b"): ["//b/sub:t2"],
            },
        )

        # Check Android.bp paths
        # //a:t1 -> a/Android.bp
        # //b/sub:t2 -> b/sub/Android.bp
        self.assertEqual(
            result.android_bp_files_by_repo,
            {
                Path("a"): [Path("a/Android.bp")],
                Path("b"): [Path("b/sub/Android.bp")],
            },
        )


class LookupOwnersTest(unittest.TestCase):
    """Tests for lookup_owners_for_git_repos."""

    @mock.patch.object(find_owners, "fetch_all_likely_relevant_code_owners")
    @mock.patch.object(find_owners.RepoCache, "create_from_manifest")
    def test_lookup_owners(
        self, mock_create_repo_cache: mock.Mock, mock_fetch_owners: mock.Mock
    ) -> None:
        android_tree = Path("/android")
        bps = {
            Path("a"): [Path("a/Android.bp"), Path("a/sub/Android.bp")],
            Path("b"): [Path("b/Android.bp")],
        }

        mock_fetch_owners.return_value = {
            "a": "owner_a",
            "b": "owner_b",
        }

        owners = file_new_warning_exemption_bugs.lookup_owners_for_git_repos(
            android_tree, bps
        )

        self.assertEqual(
            owners,
            {
                Path("a"): "owner_a",
                Path("b"): "owner_b",
            },
        )

        # Verify arguments to fetch_all_likely_relevant_code_owners.
        # It expects (repo_cache, host, check_files)
        # check_files is dict[str, list[str]] (files relative to repo)
        expected_check_files = {
            "a": ["Android.bp", "sub/Android.bp"],
            "b": ["Android.bp"],
        }

        mock_fetch_owners.assert_called_once_with(
            mock_create_repo_cache.return_value,
            find_owners.INTERNAL_GERRIT_HOST,
            expected_check_files,
        )
