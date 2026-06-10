# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for download_failing_build_logs.py."""

import concurrent.futures
import datetime
from pathlib import Path
import unittest
from unittest import mock

from bot_tools import download_failing_build_logs
from llvm_tools import cros_cls


class DownloadFailingBuildLogsTests(unittest.TestCase):
    """Tests for download_failing_build_logs."""

    def test_sanitize_step_name(self) -> None:
        self.assertEqual(
            download_failing_build_logs.sanitize_step_name("hello-world"),
            "hello-world",
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
        base_dir = Path("/tmp/logs")
        assignments = download_failing_build_logs.StepFileAssignments(base_dir)

        path1 = assignments.assign_log_file(111, "my-builder", "build-step")
        self.assertEqual(path1, Path("/tmp/logs/111/my-builder/build-step.log"))

        path2 = assignments.assign_log_file(111, "my-builder", "build|step")
        self.assertEqual(path2, Path("/tmp/logs/111/my-builder/build_step.log"))

        path3 = assignments.assign_log_file(111, "my-builder", "build#step")
        self.assertEqual(
            path3, Path("/tmp/logs/111/my-builder/build_step_1.log")
        )

        path4 = assignments.assign_log_file(222, "my-builder", "test-step")
        self.assertEqual(path4, Path("/tmp/logs/222/my-builder/test-step.log"))


@mock.patch.object(cros_cls, "fetch_sibling_builds", autospec=True)
@mock.patch.object(cros_cls, "CQOrchestratorOutput", autospec=True)
@mock.patch.object(cros_cls, "CQBoardBuilderOutput", autospec=True)
@mock.patch.object(cros_cls, "fetch_builder_steps", autospec=True)
class TestFindFailingBuildersAndSteps(unittest.TestCase):
    """Tests for find_failing_builders_and_steps."""

    def test_duplicate_child_builders_excluded(
        self,
        mock_fetch_builder_steps: mock.Mock,
        mock_board_output: mock.Mock,
        mock_orch_output: mock.Mock,
        mock_fetch_siblings: mock.Mock,
    ) -> None:
        time_now = datetime.datetime.now(datetime.timezone.utc)

        # Mock CQOrchestratorOutput.fetch
        mock_orch_instance = mock.Mock()
        mock_orch_instance.cq_attempt_key = "attempt_1"
        mock_orch_instance.child_builders = {"brya-cq": 101}
        mock_orch_output.fetch.return_value = mock_orch_instance

        mock_fetch_siblings.return_value = [
            cros_cls.BbLsInfo(
                build_id=100,
                status=cros_cls.BuilderStatus.SUCCESS,
                create_time=time_now,
                builder_name="cq-orchestrator",
            ),
            cros_cls.BbLsInfo(
                build_id=101,
                status=cros_cls.BuilderStatus.FAILURE,
                create_time=time_now,
                builder_name="brya-cq",
            ),
        ]

        mock_board_instance = mock.Mock()
        mock_board_instance.status = cros_cls.BuilderStatus.FAILURE
        mock_board_output.fetch_many.return_value = [mock_board_instance]

        mock_fetch_builder_steps.return_value = [
            {"name": "compile", "status": "FAILURE"}
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            builder_steps = (
                download_failing_build_logs.find_failing_builders_and_steps(
                    executor,
                    orchestrator_id=100,
                    build_id=None,
                    search_siblings=True,
                )
            )

        self.assertEqual(
            len(builder_steps), 1, f"Expected 1 builder, got: {builder_steps}"
        )
        self.assertEqual(builder_steps[0].build_id, 101)
        self.assertEqual(builder_steps[0].top_level_build_id, 100)

    def test_search_siblings_disabled(
        self,
        mock_fetch_builder_steps: mock.Mock,
        mock_board_output: mock.Mock,
        mock_orch_output: mock.Mock,
        mock_fetch_siblings: mock.Mock,
    ) -> None:
        mock_orch_instance = mock.Mock()
        mock_orch_instance.cq_attempt_key = "attempt_1"
        mock_orch_instance.child_builders = {"brya-cq": 101}
        mock_orch_output.fetch.return_value = mock_orch_instance

        mock_board_instance = mock.Mock()
        mock_board_instance.status = cros_cls.BuilderStatus.FAILURE
        mock_board_output.fetch_many.return_value = [mock_board_instance]

        mock_fetch_builder_steps.return_value = [
            {"name": "compile", "status": "FAILURE"}
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            builder_steps = (
                download_failing_build_logs.find_failing_builders_and_steps(
                    executor,
                    orchestrator_id=100,
                    build_id=None,
                    search_siblings=False,
                )
            )

        self.assertEqual(len(builder_steps), 1)
        self.assertEqual(builder_steps[0].build_id, 101)
        self.assertEqual(builder_steps[0].top_level_build_id, 100)
        mock_fetch_siblings.assert_not_called()
