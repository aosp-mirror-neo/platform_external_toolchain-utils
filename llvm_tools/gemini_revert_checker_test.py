# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for gemini_revert_checker."""

import datetime
import json
import subprocess
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


class EnsureStatePopulatedForTest(test_helpers.TempDirTestCase):
    """Tests for ensure_state_populated_for."""

    def setUp(self):
        self.llvm_dir = self.make_tempdir()
        self.gemini_endpoint = gemini_revert_checker.GeminiEndpoint(
            gemini_api_key="test-key"
        )

    @mock.patch.object(
        gemini_revert_checker,
        "_list_shas_between_all_of",
        return_value=["a", "b"],
    )
    @mock.patch.object(gemini_revert_checker, "_find_commits_reverted_by")
    def test_successful_population(self, find_commits_mock, list_shas_mock):
        state = gemini_revert_checker.GeminiState()
        find_commits_mock.return_value = {"a": _ARBITRARY_INFERENCE_RESULT}
        result = gemini_revert_checker.ensure_state_populated_for(
            self.gemini_endpoint,
            state,
            self.llvm_dir,
            "main",
            prepopulate_parent_shas=["c"],
        )
        self.assertTrue(result)
        self.assertEqual(
            state,
            gemini_revert_checker.GeminiState(
                revert_status={"a": _ARBITRARY_INFERENCE_RESULT}
            ),
        )
        list_shas_mock.assert_called_once_with(
            self.llvm_dir, "main", shas=["c"]
        )
        find_commits_mock.assert_called_once_with(
            self.gemini_endpoint, self.llvm_dir, commit_shas=["a", "b"]
        )

    @mock.patch.object(
        gemini_revert_checker,
        "_normalize_gemini_result",
        side_effect=lambda x, _, __: x,
    )
    @mock.patch.object(
        gemini_revert_checker,
        "_list_shas_between_all_of",
        return_value=["a", "b"],
    )
    @mock.patch.object(subprocess, "run")
    def test_partial_population(
        self, subprocess_run_mock, list_shas_mock, normalize_gemini_result_mock
    ):
        """Verifies that valid JSON entries are stored in state on failure."""
        state = gemini_revert_checker.GeminiState()

        def subprocess_run_impl(command, **_):
            # The first call is to establish_venv.sh, which we can ignore.
            if "establish_venv.sh" in str(command[0]):
                return subprocess.CompletedProcess(
                    command, 0, stdout="/tmp/venv"
                )

            # The second call is to check_reverts.py. Write our partial result
            # to the output file and simulate a failure.
            output_file = command[command.index("-o") + 1]
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "sha": "a",
                        "result": _ARBITRARY_INFERENCE_RESULT.to_json(),
                    },
                    f,
                )
                # Write a partial JSON object.
                f.write("\n{")
            return subprocess.CompletedProcess(command, 1)

        subprocess_run_mock.side_effect = subprocess_run_impl

        result = gemini_revert_checker.ensure_state_populated_for(
            self.gemini_endpoint,
            state,
            self.llvm_dir,
            "main",
            prepopulate_parent_shas=["c"],
        )
        self.assertFalse(result)
        self.assertEqual(
            state,
            gemini_revert_checker.GeminiState(
                revert_status={"a": _ARBITRARY_INFERENCE_RESULT}
            ),
        )
        list_shas_mock.assert_called_once_with(
            self.llvm_dir, "main", shas=["c"]
        )
        normalize_gemini_result_mock.assert_called()

    @mock.patch.object(gemini_revert_checker, "_list_shas_between_all_of")
    @mock.patch.object(gemini_revert_checker, "_find_commits_reverted_by")
    def test_no_shas_to_populate_returns_true(
        self, find_commits_mock, list_shas_mock
    ):
        state = gemini_revert_checker.GeminiState()
        list_shas_mock.return_value = []
        result = gemini_revert_checker.ensure_state_populated_for(
            self.gemini_endpoint,
            state,
            self.llvm_dir,
            "main",
            prepopulate_parent_shas=[],
        )
        self.assertTrue(result)
        self.assertEqual(state, gemini_revert_checker.GeminiState())
        list_shas_mock.assert_called_once_with(self.llvm_dir, "main", shas=[])
        find_commits_mock.assert_not_called()
