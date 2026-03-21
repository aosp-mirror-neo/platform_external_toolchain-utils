# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for bot_lints."""

from collections.abc import Sequence
import datetime
import json
import subprocess
from typing import Any
import unittest
from unittest import mock

from bot_tools import bot_lints


ARBITRARY_CREATE_TIME = datetime.datetime(2000, 1, 1, 0, 0, 0)
ARBITRARY_CREATE_TIME_STR = ARBITRARY_CREATE_TIME.isoformat()

ARBITRARY_FINDING = bot_lints.Finding(
    category="mock category",
    file_path="mock file path",
    gerrit_host="mock host",
    gerrit_change_number=24,
    gerrit_patchset=42,
    message="mock message",
    severity_level="mock severity level",
)


class Test(unittest.TestCase):
    """Tests for bot_lints."""

    @mock.patch.object(subprocess, "run")
    def test_fetch_bot_findings_works_as_intended(
        self, mock_run: mock.MagicMock
    ) -> None:
        def mock_run_impl(
            cmd: Sequence[str], *args: Any, **kwargs: Any
        ) -> mock.MagicMock:
            del args
            del kwargs

            if cmd[:2] == ("bb", "log"):
                stdout_json: dict[str, Any] = {
                    "findings": [
                        {
                            "category": "mock category",
                            "location": {
                                "file_path": "mock file path",
                                "gerrit_change_ref": {
                                    "host": "mock host",
                                    "patchset": "42",
                                    "change": "24",
                                },
                            },
                            "message": "mock message",
                            "severity_level": "mock severity level",
                        }
                    ]
                }
            elif cmd[:2] == ("bb", "get"):
                step_name = bot_lints.UPLOAD_LINTER_FINDINGS_STEP_NAME
                stdout_json = {
                    "createTime": ARBITRARY_CREATE_TIME_STR,
                    "steps": [
                        {"name": "foo"},
                        {"name": step_name},
                    ],
                }
            else:
                self.fail(f"Unexpected command: {cmd}")
            result = mock.MagicMock()
            result.stdout = json.dumps(stdout_json)
            return result

        mock_run.side_effect = mock_run_impl

        bot_info = bot_lints.fetch_bot_info(1234)
        self.assertEqual(
            bot_info,
            bot_lints.LinterBotInfo(
                create_time=ARBITRARY_CREATE_TIME,
                findings=[
                    bot_lints.Finding(
                        category="mock category",
                        file_path="mock file path",
                        gerrit_host="mock host",
                        gerrit_change_number=24,
                        gerrit_patchset=42,
                        message="mock message",
                        severity_level="mock severity level",
                    )
                ],
            ),
        )
        self.assertEqual(mock_run.call_count, 2)

    @mock.patch.object(subprocess, "run")
    def test_findings_arent_fetched_if_no_finding_step(
        self, mock_run: mock.MagicMock
    ) -> None:
        def mock_run_impl(
            cmd: Sequence[str], *args: Any, **kwargs: Any
        ) -> mock.MagicMock:
            del args
            del kwargs

            self.assertEqual(cmd[:2], ("bb", "get"))
            stdout = json.dumps(
                {
                    "createTime": ARBITRARY_CREATE_TIME_STR,
                    "steps": [{"name": "not a findings step, nope"}],
                }
            )
            result = mock.MagicMock()
            result.stdout = stdout
            return result

        mock_run.side_effect = mock_run_impl

        bot_info = bot_lints.fetch_bot_info(1234)
        self.assertEqual(
            bot_info,
            bot_lints.LinterBotInfo(
                create_time=ARBITRARY_CREATE_TIME,
                findings=[],
            ),
        )
        self.assertEqual(mock_run.call_count, 1)
