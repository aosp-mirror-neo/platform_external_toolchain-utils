# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for the gs module."""

import datetime
import subprocess
import unittest
from unittest import mock

from cros_utils import gs


# Protected access in tests is fine.
# pylint: disable=protected-access


class Test(unittest.TestCase):
    """Tests for the gs module."""

    def test_gs_time_parsing(self):
        self.assertEqual(
            gs._datetime_from_gs_time("2024-03-04T10:38:50Z"),
            datetime.datetime(
                year=2024,
                month=3,
                day=4,
                hour=10,
                minute=38,
                second=50,
                tzinfo=datetime.timezone.utc,
            ),
        )

    @mock.patch.object(subprocess, "run")
    def test_ls_handles_no_matches(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stderr="\nCommandException: One or more URLs matched no objects.\n",
        )
        self.assertEqual(
            gs.ls("gs://here/path/does/not/exist"),
            [],
        )

    @mock.patch.object(subprocess, "run")
    def test_ls_works_on_single_file(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            # Don't use textwrap.dedent; linter complains about the line being
            # too long in that case.
            stdout="""
753112  2024-03-04T10:38:50Z gs://here/5.4/R124-15786.10-1709548729.gcov.xz
TOTAL: 2 objects, 1234 bytes (1.1KiB)
""",
        )
        self.assertEqual(
            gs.ls("gs://here/5.4/R124-15786.10-1709548729.gcov.xz"),
            [
                gs.GsEntry(
                    last_modified=gs._datetime_from_gs_time(
                        "2024-03-04T10:38:50Z"
                    ),
                    gs_path="gs://here/5.4/R124-15786.10-1709548729.gcov.xz",
                ),
            ],
        )

    @mock.patch.object(subprocess, "run")
    def test_ls_works_on_dir(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="""
     0  2024-03-04T10:38:49Z gs://here/5.4/
753112  2024-03-04T10:38:50Z gs://here/5.4/R124-15786.10-1709548729.gcov.xz
TOTAL: 2 objects, 1234 bytes (1.1KiB)
""",
        )
        self.assertEqual(
            gs.ls("gs://here/5.4"),
            [
                gs.GsEntry(
                    last_modified=gs._datetime_from_gs_time(
                        "2024-03-04T10:38:49Z"
                    ),
                    gs_path="gs://here/5.4/",
                ),
                gs.GsEntry(
                    last_modified=gs._datetime_from_gs_time(
                        "2024-03-04T10:38:50Z"
                    ),
                    gs_path="gs://here/5.4/R124-15786.10-1709548729.gcov.xz",
                ),
            ],
        )

    @mock.patch.object(subprocess, "run")
    def test_ls_works_with_subdirs(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="""
     0  2024-03-04T10:38:49Z gs://here/
                             gs://here/5.4/
TOTAL: 2 objects, 1234 bytes (1.1KiB)
""",
        )
        self.assertEqual(
            gs.ls("gs://here/"),
            [
                gs.GsEntry(
                    last_modified=gs._datetime_from_gs_time(
                        "2024-03-04T10:38:49Z"
                    ),
                    gs_path="gs://here/",
                ),
                gs.GsEntry(
                    last_modified=None,
                    gs_path="gs://here/5.4/",
                ),
            ],
        )

    @mock.patch.object(subprocess, "run")
    def test_ls_works_with_globs(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="""
gs://here/5.4/:
     0  2024-03-04T10:38:49Z gs://here/5.4/
753112  2024-03-04T10:38:50Z gs://here/5.4/R124-15786.10-1709548729.gcov.xz
TOTAL: 2 objects, 1234 bytes (1.1KiB)
""",
        )
        self.assertEqual(
            gs.ls("gs://here/*/"),
            [
                gs.GsEntry(
                    last_modified=gs._datetime_from_gs_time(
                        "2024-03-04T10:38:49Z"
                    ),
                    gs_path="gs://here/5.4/",
                ),
                gs.GsEntry(
                    last_modified=gs._datetime_from_gs_time(
                        "2024-03-04T10:38:50Z"
                    ),
                    gs_path="gs://here/5.4/R124-15786.10-1709548729.gcov.xz",
                ),
            ],
        )
