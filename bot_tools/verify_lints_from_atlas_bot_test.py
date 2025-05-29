# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for verify_lints_from_atlas_bot.py."""

import re
import unittest

from bot_tools import bot_lints
from bot_tools import verify_lints_from_atlas_bot as verify_lints


def make_finding(
    category: str,
    file_path: str,
    message: str,
) -> bot_lints.Finding:
    """Creates a Finding instance with default values."""
    return bot_lints.Finding(
        category=category,
        file_path=file_path,
        gerrit_host="mock gerrit host",
        gerrit_change_number=123,
        gerrit_patchset=45,
        message=message,
        severity_level="SEVERITY_LEVEL_WARNING",
    )


class LogErrorsWithLintsTest(unittest.TestCase):
    """Tests for log_errors_with_lints."""

    def test_lints_match_expectations(self):
        lints = [
            make_finding(category="cat1", file_path="f1", message="msg1 indeed")
        ]
        expectations = [
            verify_lints.FindingExpectations(
                category="cat1",
                file_path="f1",
                message_re=re.compile("msg1"),
            )
        ]
        self.assertFalse(
            verify_lints.log_errors_with_lints(
                lints=lints, finding_expectations=expectations
            )
        )

    def test_unexpected_lint_with_specific_expectations(self):
        """Tests behavior with an unexpected lint and specific expectations.

        Expected:
        - Logs "No lints found for category cat1".
        - Logs "Unexpected lints found: {'unexpected': [Finding(...)]}".
        Returns True.
        """
        lints = [
            make_finding(
                category="unexpected", file_path="uf1", message="umsg1"
            )
        ]
        expectations = [
            verify_lints.FindingExpectations(
                category="cat1",
                file_path="f1",
                message_re=re.compile("msg1"),
            )
        ]
        self.assertTrue(
            verify_lints.log_errors_with_lints(
                lints=lints, finding_expectations=expectations
            )
        )

    def test_unexpected_lint(self):
        lints = [
            make_finding(
                category="unexpected", file_path="uf1", message="umsg1"
            )
        ]
        self.assertTrue(
            verify_lints.log_errors_with_lints(
                lints=lints, finding_expectations=()
            )
        )

    def test_missing_expected_lint(self):
        lints = [
            make_finding(category="cat2", file_path="f2", message="m2"),
        ]
        expectations = [
            verify_lints.FindingExpectations(
                category="cat1",
                file_path="f1",
                message_re=re.compile("msg1"),
            ),
            verify_lints.FindingExpectations(
                category="cat2",
                file_path="f2",
                message_re=re.compile("m2"),
            ),
        ]
        self.assertTrue(
            verify_lints.log_errors_with_lints(
                lints=lints, finding_expectations=expectations
            )
        )

    def test_mismatched_lint_message(self):
        lints = [
            make_finding(category="cat2", file_path="f2", message="m2"),
            make_finding(category="cat1", file_path="f1", message="wrong_msg"),
        ]
        expectations = [
            verify_lints.FindingExpectations(
                category="cat1",
                file_path="f1",
                message_re=re.compile("msg1"),
            ),
            verify_lints.FindingExpectations(
                category="cat2",
                file_path="f2",
                message_re=re.compile("m2"),
            ),
        ]
        self.assertTrue(
            verify_lints.log_errors_with_lints(
                lints=lints, finding_expectations=expectations
            )
        )


if __name__ == "__main__":
    unittest.main()
