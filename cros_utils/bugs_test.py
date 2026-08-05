# Copyright 2021 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# We're testing protected methods, so allow protected access.
# pylint: disable=protected-access

"""Tests bug filing bits."""

import datetime
import json
import os
from pathlib import Path
import textwrap
from typing import Any
from unittest import mock

from cros_utils import bugs
from llvm_tools import test_helpers


_ARBITRARY_DATETIME = datetime.datetime(2020, 1, 1, 23, 0, 0, 0)


class Tests(test_helpers.TempDirTestCase):
    """Tests for the bugs module."""

    def testWritingJSONFileSeemsToWork(self) -> None:
        """Tests JSON file writing."""
        tempdir = self.make_tempdir()

        file_path = bugs._WriteBugJSONFile(
            "ObjectType",
            {
                "foo": "bar",
                "baz": bugs.WellKnownComponents.CrOSToolchainPublic,
            },
            local_directory=tempdir,
        )

        self.assertTrue(
            file_path.startswith(str(tempdir)),
            f"Expected {file_path} to start with {tempdir}",
        )

        with open(file_path, encoding="utf-8") as f:
            self.assertEqual(
                json.load(f),
                {
                    "type": "ObjectType",
                    "value": {
                        "foo": "bar",
                        "baz": int(
                            bugs.WellKnownComponents.CrOSToolchainPublic
                        ),
                    },
                },
            )

    @mock.patch.object(bugs, "_WriteBugJSONFile")
    def testAppendingToBugsSeemsToWork(
        self, mock_write_json_file: mock.MagicMock
    ) -> None:
        """Tests AppendToExistingBug."""
        bugs.AppendToExistingBug(1234, "hello, world!")
        mock_write_json_file.assert_called_once_with(
            "AppendToExistingBugRequest",
            {
                "body": "hello, world!",
                "bug_id": 1234,
            },
            None,
        )

    @mock.patch.object(bugs, "_WriteBugJSONFile")
    def testBugCreationSeemsToWork(
        self, mock_write_json_file: mock.MagicMock
    ) -> None:
        """Tests CreateNewBug."""
        test_cases: tuple[tuple[dict[str, Any], dict[str, Any]], ...] = (
            # 1. Default test case (defaults to BUG, P2, S2)
            (
                {"component_id": 123, "title": "foo", "body": "bar"},
                {
                    "component_id": 123,
                    "subject": "foo",
                    "body": "bar",
                    "issue_type": "BUG",
                    "priority": "P2",
                    "severity": "S2",
                },
            ),
            # 2. String Enum test case
            (
                {
                    "component_id": 123,
                    "title": "foo",
                    "body": "bar",
                    "issue_type": bugs.IssueType.PROCESS,
                    "priority": bugs.Priority.P4,
                    "severity": bugs.Severity.S4,
                },
                {
                    "component_id": 123,
                    "subject": "foo",
                    "body": "bar",
                    "issue_type": "PROCESS",
                    "priority": "P4",
                    "severity": "S4",
                },
            ),
        )

        for input_kwargs, expected_output in test_cases:
            bugs.CreateNewBug(**input_kwargs)
            mock_write_json_file.assert_called_once_with(
                "FileNewBugRequest",
                expected_output,
                None,
            )
            mock_write_json_file.reset_mock()

    @mock.patch.object(bugs, "_WriteBugJSONFile")
    def testCronjobLogSendingSeemsToWork(
        self, mock_write_json_file: mock.MagicMock
    ) -> None:
        """Tests SendCronjobLog."""
        bugs.SendCronjobLog("my_name", False, "hello, world!")
        mock_write_json_file.assert_called_once_with(
            "CronjobUpdate",
            {
                "name": "my_name",
                "message": "hello, world!",
                "failed": False,
            },
            None,
        )

    @mock.patch.object(bugs, "_WriteBugJSONFile")
    def testCronjobLogSendingSeemsToWorkWithTurndown(
        self, mock_write_json_file: mock.MagicMock
    ) -> None:
        """Tests SendCronjobLog."""
        bugs.SendCronjobLog(
            "my_name", False, "hello, world!", turndown_time_hours=42
        )
        mock_write_json_file.assert_called_once_with(
            "CronjobUpdate",
            {
                "name": "my_name",
                "message": "hello, world!",
                "failed": False,
                "cronjob_turndown_time_hours": 42,
            },
            None,
        )

    @mock.patch.object(bugs, "_WriteBugJSONFile")
    def testCronjobLogSendingSeemsToWorkWithParentBug(
        self, mock_write_json_file: mock.MagicMock
    ) -> None:
        """Tests SendCronjobLog."""
        bugs.SendCronjobLog("my_name", False, "hello, world!", parent_bug=42)
        mock_write_json_file.assert_called_once_with(
            "CronjobUpdate",
            {
                "name": "my_name",
                "message": "hello, world!",
                "failed": False,
                "parent_bug": 42,
            },
            None,
        )

    def testFileNameGenerationProducesFileNamesInSortedOrder(self) -> None:
        """Tests that _FileNameGenerator gives us sorted file names."""
        gen = bugs._FileNameGenerator()
        first = gen.generate_json_file_name(_ARBITRARY_DATETIME)
        second = gen.generate_json_file_name(_ARBITRARY_DATETIME)
        self.assertLess(first, second)

    def testFileNameGenerationProtectsAgainstRipplingAdds(self) -> None:
        """Tests that _FileNameGenerator gives us sorted file names."""
        gen = bugs._FileNameGenerator()
        gen._entropy = 9
        first = gen.generate_json_file_name(_ARBITRARY_DATETIME)
        second = gen.generate_json_file_name(_ARBITRARY_DATETIME)
        self.assertLess(first, second)

        gen = bugs._FileNameGenerator()
        all_9s = "9" * (gen._ENTROPY_STR_SIZE - 1)
        gen._entropy = int(all_9s)
        third = gen.generate_json_file_name(_ARBITRARY_DATETIME)
        self.assertLess(second, third)

        fourth = gen.generate_json_file_name(_ARBITRARY_DATETIME)
        self.assertLess(third, fourth)

    @mock.patch.object(os, "getpid")
    def testForkingProducesADifferentReport(
        self, mock_getpid: mock.MagicMock
    ) -> None:
        """Tests that _FileNameGenerator gives us sorted file names."""
        gen = bugs._FileNameGenerator()

        mock_getpid.return_value = 1
        gen._entropy = 0
        parent_file = gen.generate_json_file_name(_ARBITRARY_DATETIME)

        mock_getpid.return_value = 2
        gen._entropy = 0
        child_file = gen.generate_json_file_name(_ARBITRARY_DATETIME)
        self.assertNotEqual(parent_file, child_file)

    @mock.patch.object(bugs, "_WriteBugJSONFile")
    def testCustomDirectoriesArePassedThrough(
        self, mock_write_json_file: mock.MagicMock
    ) -> None:
        directory = Path("/path/to/somewhere/interesting")
        bugs.AppendToExistingBug(1, "foo", local_directory=directory)
        mock_write_json_file.assert_called_once_with(
            mock.ANY, mock.ANY, directory
        )
        mock_write_json_file.reset_mock()

        bugs.CreateNewBug(1, "title", "body", local_directory=directory)
        mock_write_json_file.assert_called_once_with(
            mock.ANY, mock.ANY, directory
        )
        mock_write_json_file.reset_mock()

        bugs.SendCronjobLog(
            "cronjob", False, "message", local_directory=directory
        )
        mock_write_json_file.assert_called_once_with(
            mock.ANY, mock.ANY, directory
        )

    def testWriteBugJSONFileWritesToGivenDirectory(self) -> None:
        tempdir = self.make_tempdir()
        bugs.AppendToExistingBug(1, "body", local_directory=tempdir)
        json_files = list(tempdir.glob("*.json"))
        self.assertEqual(len(json_files), 1, json_files)

    def test_format_bug_works(self) -> None:
        b = bugs.format_bug(
            title="[title]",
            body="[body]",
            component=123,
            assignee="[assignee]",
            parent=321,
            priority=bugs.Priority.P1,
        )
        expected_body = textwrap.dedent(
            """\
            [title]

            [body]

            COMPONENT=123
            TYPE=INTERNAL_CLEANUP
            PRIORITY=P1
            SEVERITY=S2
            ASSIGNEE=[assignee]
            PARENT+=321
            """
        )
        self.assertEqual(b, expected_body)

    def test_format_bug_defaults(self) -> None:
        b = bugs.format_bug(
            title="[title]",
            body="[body]",
            component=123,
            parent=321,
        )
        expected_body = textwrap.dedent(
            """\
            [title]

            [body]

            COMPONENT=123
            TYPE=INTERNAL_CLEANUP
            PRIORITY=P2
            SEVERITY=S2
            PARENT+=321
            """
        )
        self.assertEqual(b, expected_body)
