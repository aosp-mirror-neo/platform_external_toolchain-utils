# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for gemini_revert_checker."""

import datetime
from unittest import mock

from llvm_tools import gemini_revert_checker
from llvm_tools import test_helpers


_ARBITRARY_INFERENCE_RESULT = gemini_revert_checker.GeminiRevertInference()


def arbitrary_time() -> datetime.datetime:
    """Returns an arbitrary datetime, in UTC."""
    return datetime.datetime(2020, 1, 2, 3, 4, 5, 6, datetime.timezone.utc)


class Test(test_helpers.TempDirTestCase):
    """Tests for the GeminiState class."""

    def test_empty_gemini_state_json_round_trips(self):
        # Test with an empty revert_status
        empty_state = gemini_revert_checker.GeminiState()
        empty_json = empty_state.to_json()
        new_empty_state = gemini_revert_checker.GeminiState.from_json(
            empty_json
        )
        self.assertEqual(empty_state, new_empty_state)

    def test_gemini_state_json_round_trips(self):
        state = gemini_revert_checker.GeminiState(
            revert_status={
                "sha123": gemini_revert_checker.GeminiRevertInference(
                    reverted_shas=("sha456",),
                    reverted_prs=(),
                    is_revert=True,
                ),
                "sha789": gemini_revert_checker.GeminiRevertInference(
                    reverted_shas=("shaabc", "shadef"),
                    reverted_prs=(),
                    is_revert=True,
                ),
            }
        )
        state_json = state.to_json()
        new_state = gemini_revert_checker.GeminiState.from_json(state_json)
        self.assertEqual(state, new_state)


class DiscardOldShasTest(test_helpers.TempDirTestCase):
    """Tests for discard_old_shas."""

    def setUp(self):
        self.llvm_dir = self.make_tempdir()
        # Don't need to init a git repo; we're mocking all git operations.

    @mock.patch.object(gemini_revert_checker, "_list_shas_between_all_of")
    def test_no_shas_discards_attempted_if_not_expired(self, list_shas_mock):
        now = arbitrary_time()
        state = gemini_revert_checker.GeminiState(
            revert_status={"a": _ARBITRARY_INFERENCE_RESULT},
            important_shas={"c": int(now.timestamp())},
        )
        gemini_revert_checker.discard_old_shas(
            state,
            currently_important_shas=["d"],
            now=now,
            llvm_dir=self.llvm_dir,
            main_ref="main",
        )
        self.assertEqual(
            state,
            gemini_revert_checker.GeminiState(
                revert_status={"a": _ARBITRARY_INFERENCE_RESULT},
                important_shas={
                    "c": int(now.timestamp()),
                    "d": int(now.timestamp()),
                },
            ),
        )
        list_shas_mock.assert_not_called()

    @mock.patch.object(
        gemini_revert_checker, "_list_shas_between_all_of", return_value=["c"]
    )
    def test_shas_discarded_after_expiration(self, list_shas_mock):
        now = arbitrary_time()
        state = gemini_revert_checker.GeminiState(
            revert_status={
                "a": _ARBITRARY_INFERENCE_RESULT,
                "c": _ARBITRARY_INFERENCE_RESULT,
            },
            important_shas={
                "c": int(
                    (
                        now
                        - gemini_revert_checker.INFERENCE_COLLECTION_THRESHOLD
                        - gemini_revert_checker.datetime.timedelta(days=1)
                    ).timestamp()
                )
            },
        )
        gemini_revert_checker.discard_old_shas(
            state,
            currently_important_shas=["b"],
            now=now,
            llvm_dir=self.llvm_dir,
            main_ref="main",
        )
        self.assertEqual(
            state,
            gemini_revert_checker.GeminiState(
                revert_status={"c": _ARBITRARY_INFERENCE_RESULT},
                important_shas={"b": int(now.timestamp())},
            ),
        )
        list_shas_mock.assert_called_once_with(
            self.llvm_dir, "main", shas=["b"]
        )

    @mock.patch.object(
        gemini_revert_checker,
        "_list_shas_between_all_of",
        return_value=["a", "b", "c"],
    )
    def test_shas_in_history_of_important_shas_are_kept(self, list_shas_mock):
        now = arbitrary_time()
        state = gemini_revert_checker.GeminiState(
            revert_status={
                "a": _ARBITRARY_INFERENCE_RESULT,
                "b": _ARBITRARY_INFERENCE_RESULT,
                "c": _ARBITRARY_INFERENCE_RESULT,
                "d": _ARBITRARY_INFERENCE_RESULT,
            },
            important_shas={
                "c": int(now.timestamp()),
                "d": int(
                    (
                        now
                        - gemini_revert_checker.INFERENCE_COLLECTION_THRESHOLD
                        - gemini_revert_checker.datetime.timedelta(days=1)
                    ).timestamp()
                ),
            },
        )
        gemini_revert_checker.discard_old_shas(
            state,
            currently_important_shas=["c"],
            now=now,
            llvm_dir=self.llvm_dir,
            main_ref="main",
        )
        self.assertEqual(
            state,
            gemini_revert_checker.GeminiState(
                revert_status={
                    "a": _ARBITRARY_INFERENCE_RESULT,
                    "b": _ARBITRARY_INFERENCE_RESULT,
                    "c": _ARBITRARY_INFERENCE_RESULT,
                },
                important_shas={"c": int(now.timestamp())},
            ),
        )
        list_shas_mock.assert_called_once_with(
            self.llvm_dir, "main", shas=["c"]
        )
