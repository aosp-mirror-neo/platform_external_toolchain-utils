# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for file_new_warning_exemption_bugs."""

from pathlib import Path
import unittest
from unittest import mock

from android_tools import file_new_warning_exemption_bugs
from android_tools import find_owners
from android_tools import gerrit_utils


class LookupOwnersTest(unittest.TestCase):
    """Tests for lookup_owners_for_git_repos."""

    @mock.patch.object(find_owners, "fetch_all_likely_relevant_code_owners")
    @mock.patch.object(find_owners.RepoCache, "create_from_manifest")
    def test_lookup_owners(
        self, mock_create_repo_cache: mock.Mock, mock_fetch_owners: mock.Mock
    ) -> None:
        android_tree = Path("/android")
        bps = {
            Path("a"): ["//a:foo", "//a/sub:bar"],
            Path("b"): ["//b:baz"],
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
            gerrit_utils.INTERNAL_GERRIT_HOST,
            expected_check_files,
        )
