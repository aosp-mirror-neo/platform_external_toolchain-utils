# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for fetch_atlas_linter_cq_findings."""

import dataclasses
import datetime

from bot_tools import bot_lints
from bot_tools import fetch_atlas_linter_cq_findings as fetch_findings
from llvm_tools import test_helpers


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


class Test(test_helpers.TempDirTestCase):
    """Tests for fetch_atlas_linter_cq_findings."""

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
                bot_lints.LinterBotInfo(
                    create_time=datetime.datetime(2000, 1, 1, 0, 0, 0),
                    findings=[
                        finding1,
                    ],
                ),
                bot_lints.LinterBotInfo(
                    create_time=datetime.datetime(2000, 1, 1, 23, 59, 59),
                    findings=[
                        finding2,
                    ],
                ),
                bot_lints.LinterBotInfo(
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
