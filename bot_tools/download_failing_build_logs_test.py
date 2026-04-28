# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for download_failing_build_logs.py."""

from pathlib import Path
import unittest

from bot_tools import download_failing_build_logs


class DownloadFailingBuildLogsTests(unittest.TestCase):
    """Tests for download_failing_build_logs."""

    def test_sanitize_step_name(self) -> None:
        self.assertEqual(
            download_failing_build_logs.sanitize_step_name("hello-world"),
            "hello_world",
        )
        self.assertEqual(
            download_failing_build_logs.sanitize_step_name("foo|bar"), "foo_bar"
        )
        self.assertEqual(
            download_failing_build_logs.sanitize_step_name("valid123"),
            "valid123",
        )
        self.assertEqual(
            download_failing_build_logs.sanitize_step_name(
                "some#special$characters%^"
            ),
            "some_special_characters__",
        )

    def test_step_file_assignments_collision(self) -> None:
        out_dir = Path("/tmp/logs")
        assignments = download_failing_build_logs.StepFileAssignments(out_dir)

        path1 = assignments.assign_log_file("my-builder", "build-step")
        self.assertEqual(path1, Path("/tmp/logs/my_builder/build_step.log"))

        path2 = assignments.assign_log_file("my-builder", "build|step")
        self.assertEqual(path2, Path("/tmp/logs/my_builder/build_step_1.log"))

        path3 = assignments.assign_log_file("my-builder", "build#step")
        self.assertEqual(path3, Path("/tmp/logs/my_builder/build_step_2.log"))

        path4 = assignments.assign_log_file("my-builder", "test-step")
        self.assertEqual(path4, Path("/tmp/logs/my_builder/test_step.log"))
