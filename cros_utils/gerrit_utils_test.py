# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for gerrit_utils."""

import concurrent.futures
import json
import unittest
from unittest import mock

from cros_utils import gerrit_utils


class CLStatusTest(unittest.TestCase):
    """Tests for CLStatus."""

    def test_is_open(self) -> None:
        """Test that is_open() is correct."""
        self.assertTrue(gerrit_utils.CLStatus.NEW.is_open())
        self.assertFalse(gerrit_utils.CLStatus.MERGED.is_open())
        self.assertFalse(gerrit_utils.CLStatus.ABANDONED.is_open())

    def test_parse_roundtrip(self) -> None:
        """Test that parsing round-trips."""
        for status in gerrit_utils.CLStatus:
            self.assertEqual(status, gerrit_utils.CLStatus.parse(status.value))

    def test_parse_invalid_raises(self) -> None:
        """Test that parsing an invalid status raises."""
        with self.assertRaises(ValueError):
            gerrit_utils.CLStatus.parse("INVALID_STATUS")


class FetchRelatedChangesTest(unittest.TestCase):
    """Tests for fetch_related_changes."""

    @mock.patch.object(gerrit_utils, "fetch_gob_curl_body_with_retries")
    def test_invalid_status_raises(
        self,
        mock_fetch_gob_curl_body_with_retries: mock.Mock,
    ) -> None:
        """Test that an invalid status raises a ValueError."""
        gerrit_response = {
            "changes": [
                {
                    "project": "project/a",
                    "_change_number": 123,
                    "status": "INVALID_STATUS",
                }
            ]
        }
        mock_fetch_gob_curl_body_with_retries.return_value = (
            f")]}}'\n{json.dumps(gerrit_response)}"
        )

        with self.assertRaises(ValueError):
            gerrit_utils.fetch_related_changes("gerrit_host", 123)


class TestChangeListURL(unittest.TestCase):
    """ChangeListURL tests."""

    def test_parsing_long_form_url(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "chromium-review.googlesource.com/c/chromiumos/overlays/"
                "chromiumos-overlay/+/123456",
            ),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )

    def test_parsing_long_form_internal_url(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "chrome-internal-review.googlesource.com/c/chromeos/"
                "manifest-internal/+/654321"
            ),
            gerrit_utils.ChangeListURL(
                cl_id=654321, patch_set=None, internal=True
            ),
        )

    def test_parsing_long_form_git_corp_url(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "chromium-review.git.corp.google.com/c/chromiumos/overlays/"
                "chromiumos-overlay/+/123456",
            ),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )

    def test_parsing_long_form_git_corp_internal_url(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "chrome-internal-review.git.corp.google.com/c/chromeos/"
                "manifest-internal/+/654321"
            ),
            gerrit_utils.ChangeListURL(
                cl_id=654321, patch_set=None, internal=True
            ),
        )

    def test_parsing_short_internal_url(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/i/654321"),
            gerrit_utils.ChangeListURL(
                cl_id=654321, patch_set=None, internal=True
            ),
        )

    def test_parsing_discards_http(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("http://crrev.com/c/123456"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )

    def test_parsing_discards_https(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("https://crrev.com/c/123456"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )

    def test_parsing_detects_patch_sets(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456/14"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=14),
        )

    def test_parsing_is_okay_with_trailing_slash(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456/"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456/14/"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=14),
        )

    def test_parsing_is_okay_with_valid_trailing_junk(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456?foo=bar"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456/?foo=bar"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456/14/foo=bar"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=14),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456/14?foo=bar"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=14),
        )

        # While these aren't well-formed, Gerrit handles them without issue.
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456&foo=bar"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456/14&foo=bar"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=14),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456/14foo=bar"),
            gerrit_utils.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123/4foo"),
            gerrit_utils.ChangeListURL(cl_id=123, patch_set=None),
        )

    def test_parsing_raises_on_invalid_trailing_jumk(self) -> None:
        with self.assertRaises(ValueError):
            gerrit_utils.ChangeListURL.parse("crrev.com/c/123456foo=bar")

    def test_parsing_hash_c_url(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "https://chrome-internal-review.googlesource.com/#/c/9088380/"
            ),
            gerrit_utils.ChangeListURL(cl_id=9088380, internal=True),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "https://chromium-review.git.corp.google.com/#/c/7832690/"
            ),
            gerrit_utils.ChangeListURL(cl_id=7832690, internal=False),
        )

    def test_parsing_android_long_form(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "https://googleplex-android-review.git.corp.google.com/"
                "c/toolchain/utils/+/123456"
            ),
            gerrit_utils.ChangeListURL(
                cl_id=123456, internal=True, android=True
            ),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "https://googleplex-android-review.git.corp.google.com/"
                "c/toolchain/utils/+/123456/2"
            ),
            gerrit_utils.ChangeListURL(
                cl_id=123456, patch_set=2, internal=True, android=True
            ),
        )

    def test_parsing_android_short_form(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("ag/123456"),
            gerrit_utils.ChangeListURL(
                cl_id=123456, internal=True, android=True
            ),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("ag/c/123456"),
            gerrit_utils.ChangeListURL(
                cl_id=123456, internal=True, android=True
            ),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("go/ag/123456"),
            gerrit_utils.ChangeListURL(
                cl_id=123456, internal=True, android=True
            ),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("go/ag/c/123456"),
            gerrit_utils.ChangeListURL(
                cl_id=123456, internal=True, android=True
            ),
        )
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse("ag/123456/3"),
            gerrit_utils.ChangeListURL(
                cl_id=123456, patch_set=3, internal=True, android=True
            ),
        )

    def test_parsing_url_with_file_path_long_form(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "https://chromium-review.git.corp.google.com/c/chromiumos/"
                "overlays/chromiumos-overlay/+/7897457/30/sys-devel/llvm/"
                "files/compiler_wrapper/config.go"
            ),
            gerrit_utils.ChangeListURL(
                cl_id=7897457, patch_set=30, internal=False
            ),
        )

    def test_parsing_url_with_file_path_short_form(self) -> None:
        self.assertEqual(
            gerrit_utils.ChangeListURL.parse(
                "http://crrev.com/c/7897457/30/sys-devel/llvm/files/"
                "compiler_wrapper/config.go"
            ),
            gerrit_utils.ChangeListURL(
                cl_id=7897457, patch_set=30, internal=False
            ),
        )

    def test_str_functions_properly(self) -> None:
        self.assertEqual(
            str(
                gerrit_utils.ChangeListURL(
                    cl_id=1234,
                    patch_set=2,
                )
            ),
            "https://crrev.com/c/1234/2",
        )

        self.assertEqual(
            str(
                gerrit_utils.ChangeListURL(
                    cl_id=1234,
                    patch_set=None,
                )
            ),
            "https://crrev.com/c/1234",
        )

        self.assertEqual(
            str(
                gerrit_utils.ChangeListURL(
                    cl_id=1234,
                    patch_set=2,
                    internal=True,
                )
            ),
            "https://crrev.com/i/1234/2",
        )

        self.assertEqual(
            str(
                gerrit_utils.ChangeListURL(
                    cl_id=1234,
                    patch_set=2,
                    android=True,
                )
            ),
            "https://ag/1234/2",
        )

        self.assertEqual(
            str(
                gerrit_utils.ChangeListURL(
                    cl_id=1234,
                    patch_set=None,
                    android=True,
                )
            ),
            "https://ag/1234",
        )


class ResolveAndSortClDependenciesTest(unittest.TestCase):
    """Tests for resolve_and_sort_cl_dependencies."""

    gerrit_host = gerrit_utils.ANDROID_INTERNAL_GERRIT_HOST

    def _make_cl(
        self,
        project: str,
        cl_id: int,
        status: gerrit_utils.CLStatus = gerrit_utils.CLStatus.NEW,
    ) -> gerrit_utils.CLDetails:
        return gerrit_utils.CLDetails(
            project=project,
            cl_url=gerrit_utils.ChangeListURL.parse(
                f"{self.gerrit_host}/{cl_id}"
            ),
            status=status,
        )

    @mock.patch.object(gerrit_utils, "fetch_related_changes", autospec=True)
    def test_truncates_chain_to_child_most_cl(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies that the chain is truncated to the child-most CL."""
        cl1 = self._make_cl("project/a", 1)
        cl2 = self._make_cl("project/a", 2)
        cl3 = self._make_cl("project/a", 3)

        # The input CLs only contain 1 and 3. The chain contains 1, 2, 3, 4.
        # The child-most CL in the input is 3. So the final list should be
        # [1, 2, 3].
        cls = [cl1, cl3]
        chain_info = [
            self._make_cl("project/a", 4),
            self._make_cl("project/a", 3),
            self._make_cl("project/a", 2),
            self._make_cl("project/a", 1),
        ]
        mock_fetch_related_changes.return_value = chain_info

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = gerrit_utils.resolve_and_sort_cl_dependencies(
                cls, executor
            )

        self.assertEqual(result, [cl1, cl2, cl3])
        mock_fetch_related_changes.assert_called_once_with(self.gerrit_host, 1)

    @mock.patch.object(gerrit_utils, "fetch_related_changes", autospec=True)
    def test_no_truncation_if_child_most_is_last(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies no truncation when the child-most CL is the last one."""
        cl1 = self._make_cl("project/a", 1)
        cl2 = self._make_cl("project/a", 2)
        cl3 = self._make_cl("project/a", 3)

        cls = [cl1, cl3]
        chain_info = [
            self._make_cl("project/a", 3),
            self._make_cl("project/a", 2),
            self._make_cl("project/a", 1),
        ]
        mock_fetch_related_changes.return_value = chain_info

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = gerrit_utils.resolve_and_sort_cl_dependencies(
                cls, executor
            )

        self.assertEqual(result, [cl1, cl2, cl3])
        mock_fetch_related_changes.assert_called_once_with(self.gerrit_host, 1)

    @mock.patch.object(gerrit_utils, "fetch_related_changes", autospec=True)
    def test_single_cl_from_chain(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies correct handling when only one CL from a chain is given."""
        cl1 = self._make_cl("project/a", 1)
        cl2 = self._make_cl("project/a", 2)

        cls = [cl2]
        chain_info = [
            self._make_cl("project/a", 3),
            self._make_cl("project/a", 2),
            self._make_cl("project/a", 1),
        ]
        mock_fetch_related_changes.return_value = chain_info

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = gerrit_utils.resolve_and_sort_cl_dependencies(
                cls, executor
            )

        self.assertEqual(result, [cl1, cl2])
        mock_fetch_related_changes.assert_called_once_with(self.gerrit_host, 2)

    @mock.patch.object(gerrit_utils, "fetch_related_changes", autospec=True)
    def test_standalone_cl(self, mock_fetch_related_changes: mock.Mock) -> None:
        """Verifies correct handling of a standalone CL."""
        cl1 = self._make_cl("project/a", 1)
        cls = [cl1]
        mock_fetch_related_changes.return_value = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = gerrit_utils.resolve_and_sort_cl_dependencies(
                cls, executor
            )

        self.assertEqual(result, [cl1])
        mock_fetch_related_changes.assert_called_once_with(self.gerrit_host, 1)

    @mock.patch.object(gerrit_utils, "fetch_related_changes", autospec=True)
    def test_multiple_cls_and_projects(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies correct handling of multiple CLs in multiple projects."""
        cl1a = self._make_cl("project/a", 1)
        cl2a = self._make_cl("project/a", 2)
        cl1b = self._make_cl("project/b", 3)
        cl2b = self._make_cl("project/b", 4)

        cls = [cl2a, cl2b]

        def fetch_side_effect(
            gerrit_host: str, change_id: int
        ) -> list[gerrit_utils.CLDetails]:
            del gerrit_host  # unused
            if change_id == cl2a.cl_number:
                return [
                    self._make_cl("project/a", 2),
                    self._make_cl("project/a", 1),
                ]
            if change_id == cl2b.cl_number:
                return [
                    self._make_cl("project/b", 4),
                    self._make_cl("project/b", 3),
                ]
            return []

        mock_fetch_related_changes.side_effect = fetch_side_effect

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            result = gerrit_utils.resolve_and_sort_cl_dependencies(
                cls, executor
            )

        self.assertEqual(result, [cl1a, cl2a, cl1b, cl2b])
        self.assertEqual(mock_fetch_related_changes.call_count, 2)
        mock_fetch_related_changes.assert_has_calls(
            [mock.call(self.gerrit_host, 2), mock.call(self.gerrit_host, 4)],
            any_order=True,
        )

    @mock.patch.object(gerrit_utils, "fetch_related_changes", autospec=True)
    def test_deduplicates_overlapping_chains(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies that overlapping dependency chains are deduplicated."""
        # A (CL 1) is parent of B (CL 2) and C (CL 3).
        # We request B and C.
        cl1 = self._make_cl("project/a", 1)
        cl2 = self._make_cl("project/a", 2)
        cl3 = self._make_cl("project/a", 3)

        cls = [cl2, cl3]

        def fetch_side_effect(
            gerrit_host: str, change_id: int
        ) -> list[gerrit_utils.CLDetails]:
            del gerrit_host  # unused
            if change_id == cl2.cl_number:
                return [
                    self._make_cl("project/a", 2),
                    self._make_cl("project/a", 1),
                ]
            if change_id == cl3.cl_number:
                return [
                    self._make_cl("project/a", 3),
                    self._make_cl("project/a", 1),
                ]
            return []

        mock_fetch_related_changes.side_effect = fetch_side_effect

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            result = gerrit_utils.resolve_and_sort_cl_dependencies(
                cls, executor
            )

        # Expected: A (1) is applied first, then B (2) and C (3).
        # Since both chains start with CL 1, their relative order is based
        # on thread race.
        # But both contain 1. Deduped (keeping first) will result in
        # [1, 2, 3] or [1, 3, 2].
        # In either case, 1 is first.
        self.assertEqual(result[0], cl1)
        self.assertCountEqual(result, [cl1, cl2, cl3])
        self.assertEqual(len(result), 3)
