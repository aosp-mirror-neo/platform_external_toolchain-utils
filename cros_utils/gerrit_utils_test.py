# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for gerrit_utils."""

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
