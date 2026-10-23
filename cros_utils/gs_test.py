# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for the gs module."""

import datetime
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from cros_utils import gs


# Protected access in tests is fine.
# pylint: disable=protected-access


class Test(unittest.TestCase):
    """Tests for the gs module."""

    def test_gs_time_parsing(self) -> None:
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
    def test_ls_handles_no_matches(self, run_mock: mock.MagicMock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="\nCommandException: One or more URLs matched no objects.\n",
        )
        self.assertEqual(
            gs.ls("gs://here/path/does/not/exist"),
            [],
        )

    @mock.patch.object(subprocess, "run")
    def test_ls_works_on_single_file(self, run_mock: mock.MagicMock) -> None:
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
    def test_ls_works_on_dir(self, run_mock: mock.MagicMock) -> None:
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
    def test_ls_works_with_subdirs(self, run_mock: mock.MagicMock) -> None:
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
    def test_ls_works_with_globs(self, run_mock: mock.MagicMock) -> None:
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

    @mock.patch.object(subprocess, "run", autospec=True)
    def test_ls_works_with_subdirs_only_without_total(
        self, run_mock: mock.MagicMock
    ) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="""
                             gs://here/5.4/
                             gs://here/5.10/
""",
        )
        self.assertEqual(
            gs.ls("gs://here/"),
            [
                gs.GsEntry(
                    last_modified=None,
                    gs_path="gs://here/5.4/",
                ),
                gs.GsEntry(
                    last_modified=None,
                    gs_path="gs://here/5.10/",
                ),
            ],
        )

    @mock.patch.object(subprocess, "run", autospec=True)
    def test_ls_works_with_multiple_urls_partial_match(
        self, run_mock: mock.MagicMock
    ) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="""
753112  2024-03-04T10:38:50Z gs://here/5.4/R124-15786.10-1709548729.gcov.xz
TOTAL: 1 objects, 753112 bytes (735.46 KiB)
""",
            stderr="CommandException: One or more URLs matched no objects.\n",
        )
        self.assertEqual(
            gs.ls(
                "gs://here/5.4/R124-15786.10-1709548729.gcov.xz",
                "gs://here/does/not/exist",
            ),
            [
                gs.GsEntry(
                    last_modified=gs._datetime_from_gs_time(
                        "2024-03-04T10:38:50Z"
                    ),
                    gs_path="gs://here/5.4/R124-15786.10-1709548729.gcov.xz",
                ),
            ],
        )
        run_mock.assert_called_once_with(
            (
                gs.GSUTIL,
                "-m",
                "ls",
                "-l",
                "gs://here/5.4/R124-15786.10-1709548729.gcov.xz",
                "gs://here/does/not/exist",
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )

    @mock.patch.object(subprocess, "run", autospec=True)
    def test_cat_returns_stdout(self, run_mock: mock.MagicMock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="hello world\n",
        )
        self.assertEqual(gs.cat("gs://bucket/file"), "hello world\n")

    def test_build_cp_command_options(self) -> None:
        self.assertEqual(
            gs.build_cp_command("gs://src", Path("/dst")),
            [gs.GSUTIL, "cp", "-n", "gs://src", "/dst"],
        )
        self.assertEqual(
            gs.build_cp_command("gs://src", Path("/dst"), recursive=True),
            [gs.GSUTIL, "-m", "cp", "-r", "-n", "gs://src", "/dst"],
        )
        self.assertEqual(
            gs.build_cp_many_command(
                ["gs://src1", "gs://src2"],
                Path("/dst"),
                recursive=True,
                overwrite=True,
                public_read=True,
            ),
            [
                gs.GSUTIL,
                "-m",
                "cp",
                "-r",
                "-a",
                "public-read",
                "gs://src1",
                "gs://src2",
                "/dst",
            ],
        )

    @mock.patch.object(subprocess, "run", autospec=True)
    def test_check_world_readable_access_denied(
        self, run_mock: mock.MagicMock
    ) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="AccessDeniedException: 403 Access denied.\n",
        )
        self.assertIs(
            gs.check_world_readable("gs://bucket/file"),
            gs.WorldReadableStatus.LACKS_PERMISSION,
        )

    @mock.patch.object(subprocess, "run", autospec=True)
    def test_check_world_readable_ok(self, run_mock: mock.MagicMock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='[ { "entity": "allUsers", "role": "READER" } ]',
            stderr="",
        )
        self.assertIs(
            gs.check_world_readable("gs://bucket/file"),
            gs.WorldReadableStatus.GRANTED,
        )

    @mock.patch.object(subprocess, "run", autospec=True)
    def test_check_world_readable_missing_reader(
        self, run_mock: mock.MagicMock
    ) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='[ { "entity": "___fake@google.com", "role": "OWNER" } ]',
            stderr="",
        )
        self.assertIs(
            gs.check_world_readable("gs://bucket/file"),
            gs.WorldReadableStatus.NOT_GRANTED,
        )
