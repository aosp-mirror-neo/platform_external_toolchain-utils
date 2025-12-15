# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for verify_cq_builders_update_sdk.py."""

import unittest
from unittest import mock

from bot_tools import verify_cq_builders_update_sdk as verify_update_sdk
from llvm_tools import cros_cls


# These are unittests; protected access is fine.
# pylint: disable=protected-access


@mock.patch.object(verify_update_sdk.cros_cls.CQOrchestratorOutput, "fetch")
@mock.patch.object(verify_update_sdk.cros_cls, "fetch_builder_steps")
class InspectAndVerifyCqOrchestratorTests(unittest.TestCase):
    """Tests for _inspect_and_verify_cq_orchestrator."""

    def test_no_error_if_all_children_have_step(
        self,
        mock_fetch_builder_steps: mock.MagicMock,
        mock_cq_orch_fetch: mock.MagicMock,
    ) -> None:
        mock_cq_orch_fetch.side_effect = [
            cros_cls.CQOrchestratorOutput(
                status=cros_cls.BuilderStatus.SUCCESS,
                child_builders={
                    "builder_a": 101,
                    "builder_b": 102,
                },
            )
        ]
        mock_fetch_builder_steps.side_effect = [
            [
                {"name": "other_step"},
                {"name": verify_update_sdk.TARGET_STEP_NAME},
            ],
            [{"name": verify_update_sdk.TARGET_STEP_NAME}],
        ]

        self.assertTrue(
            verify_update_sdk._inspect_and_verify_cq_orchestrator(
                build_id=100, min_expected_child_builders=2
            )
        )

    def test_no_error_if_ignored_child_lacks_step(
        self,
        mock_fetch_builder_steps: mock.MagicMock,
        mock_cq_orch_fetch: mock.MagicMock,
    ) -> None:
        mock_cq_orch_fetch.side_effect = [
            cros_cls.CQOrchestratorOutput(
                status=cros_cls.BuilderStatus.SUCCESS,
                child_builders={
                    "builder_a": 101,
                    "chromite-cq": 102,
                    "brya-bazel-lite-cq": 103,
                },
            )
        ]
        mock_fetch_builder_steps.side_effect = [
            [
                {"name": "other_step"},
                {"name": verify_update_sdk.TARGET_STEP_NAME},
            ],
            [{"name": "other_step"}],
            [{"name": "other_step"}],
        ]

        self.assertTrue(
            verify_update_sdk._inspect_and_verify_cq_orchestrator(
                build_id=100, min_expected_child_builders=3
            )
        )

    def test_too_few_child_builders(
        self,
        mock_fetch_builder_steps: mock.MagicMock,
        mock_cq_orch_fetch: mock.MagicMock,
    ) -> None:
        mock_cq_orch_fetch.side_effect = [
            cros_cls.CQOrchestratorOutput(
                status=cros_cls.BuilderStatus.SUCCESS,
                child_builders={
                    "builder_a": 101,
                    "builder_b": 102,
                },
            )
        ]
        mock_fetch_builder_steps.side_effect = [
            [
                {"name": "other_step"},
                {"name": verify_update_sdk.TARGET_STEP_NAME},
            ],
            [{"name": verify_update_sdk.TARGET_STEP_NAME}],
        ]

        self.assertFalse(
            verify_update_sdk._inspect_and_verify_cq_orchestrator(
                build_id=100, min_expected_child_builders=3
            )
        )

    def test_builder_missing_step(
        self,
        mock_fetch_builder_steps: mock.MagicMock,
        mock_cq_orch_fetch: mock.MagicMock,
    ) -> None:
        mock_cq_orch_fetch.side_effect = [
            cros_cls.CQOrchestratorOutput(
                status=cros_cls.BuilderStatus.SUCCESS,
                child_builders={
                    "builder_a": 101,
                    "builder_b": 102,
                },
            )
        ]
        mock_fetch_builder_steps.side_effect = [
            [
                {"name": "other_step"},
            ],
            [{"name": verify_update_sdk.TARGET_STEP_NAME}],
        ]

        self.assertFalse(
            verify_update_sdk._inspect_and_verify_cq_orchestrator(
                build_id=100, min_expected_child_builders=2
            )
        )
