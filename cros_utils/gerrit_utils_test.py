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
