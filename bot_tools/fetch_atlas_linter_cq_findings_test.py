# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for fetch_atlas_linter_cq_findings."""

import dataclasses
import datetime
import json
import subprocess
from unittest import mock

from bot_tools import fetch_atlas_linter_cq_findings as fetch_findings
from llvm_tools import test_helpers


ARBITRARY_CREATE_TIME = datetime.datetime(2000, 1, 1, 0, 0, 0)
ARBITRARY_CREATE_TIME_STR = ARBITRARY_CREATE_TIME.isoformat()

ARBITRARY_FINDING = fetch_findings.Finding(
    category="mock category",
    file_path="mock file path",
    gerrit_host="mock host",
    gerrit_change_number=24,
    gerrit_patchset=42,
    message="mock message",
    severity_level="mock severity level",
)


class Test(test_helpers.TempDirTestCase):
    """Tests for fetch_atlas_linter_cq_findings."""

    @mock.patch.object(subprocess, "run")
    def test_fetch_bot_findings_works_as_intended(self, mock_run):
        def mock_run_impl(cmd, *args, **kwargs):
            del args
            del kwargs

            if cmd[:2] == ("bb", "log"):
                stdout_json = {
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
                step_name = fetch_findings.UPLOAD_LINTER_FINDINGS_STEP_NAME
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

        bot_info = fetch_findings.fetch_bot_info(1234)
        self.assertEqual(
            bot_info,
            fetch_findings.LinterBotInfo(
                create_time=ARBITRARY_CREATE_TIME,
                findings=[
                    fetch_findings.Finding(
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
    def test_findings_arent_fetched_if_no_finding_step(self, mock_run):
        def mock_run_impl(cmd, *args, **kwargs):
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

        bot_info = fetch_findings.fetch_bot_info(1234)
        self.assertEqual(
            bot_info,
            fetch_findings.LinterBotInfo(
                create_time=ARBITRARY_CREATE_TIME,
                findings=[],
            ),
        )
        self.assertEqual(mock_run.call_count, 1)

    def test_finding_grouping_works(self):
        finding1 = dataclasses.replace(
            ARBITRARY_FINDING, category="mock category 1"
        )
        finding2 = dataclasses.replace(
            ARBITRARY_FINDING, category="mock category 2"
        )
        finding3 = dataclasses.replace(
            ARBITRARY_FINDING, category="mock category 3"
        )

        grouped = fetch_findings.group_findings_by_date(
            [
                fetch_findings.LinterBotInfo(
                    create_time=datetime.datetime(2000, 1, 1, 0, 0, 0),
                    findings=[
                        finding1,
                    ],
                ),
                fetch_findings.LinterBotInfo(
                    create_time=datetime.datetime(2000, 1, 1, 23, 59, 59),
                    findings=[
                        finding2,
                    ],
                ),
                fetch_findings.LinterBotInfo(
                    create_time=datetime.datetime(2000, 1, 2, 0, 0, 0),
                    findings=[
                        finding3,
                    ],
                ),
            ]
        )
        self.assertEqual(
            grouped,
            {
                datetime.date(2000, 1, 1): [finding1, finding2],
                datetime.date(2000, 1, 2): [finding3],
            },
        )

    def test_asciify_works(self):
        nonascii = "幸"
        self.assertEqual(fetch_findings.asciify(nonascii), "\\u5e78")

    def test_grouped_findings_writing(self):
        out_dir = self.make_tempdir()
        fetch_findings.write_grouped_findings(
            out_dir,
            findings={
                datetime.date(2000, 1, 1): [],
                datetime.date(2000, 11, 12): [ARBITRARY_FINDING],
            },
        )

        self.assertFalse((out_dir / "2000" / "01" / "01").exists())
        self.assertTrue((out_dir / "2000" / "11" / "12" / "data.csv").exists())
