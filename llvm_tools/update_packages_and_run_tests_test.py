# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for update_packages_and_run_tests.py"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from llvm_tools import update_packages_and_run_tests


class Test(unittest.TestCase):
    """Tests for update_packages_and_run_tests.py"""

    def make_tempdir(self) -> Path:
        tempdir = tempfile.mkdtemp("run_llvm_tests_at_sha_test_")
        self.addCleanup(shutil.rmtree, tempdir)
        return Path(tempdir)

    def test_sha_state_file_handles_file_not_existing(self):
        tempdir = self.make_tempdir()
        self.assertIsNone(
            update_packages_and_run_tests.read_last_tried_sha(
                tempdir / "does-not-exist"
            )
        )

    def test_sha_state_file_round_trips(self):
        tempdir = self.make_tempdir()
        state_file = tempdir / "state.json"
        sha = "a" * 40
        update_packages_and_run_tests.write_last_tried_sha(state_file, sha)
        self.assertEqual(
            update_packages_and_run_tests.read_last_tried_sha(state_file), sha
        )

    @mock.patch.object(subprocess, "run")
    def test_gerrit_cq_dry_run_runs_correct_gerrit_commands(self, mock_run):
        chromeos_tree = self.make_tempdir()
        update_packages_and_run_tests.cq_dry_run_cls(
            chromeos_tree,
            update_packages_and_run_tests.UploadedCLs(
                internal=[123],
                external=[456, 789],
            ),
        )
        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_any_call(
            ["gerrit", "label-cq", "456", "789", "1"],
            check=True,
            cwd=chromeos_tree,
            stdin=subprocess.DEVNULL,
        )
        mock_run.assert_any_call(
            ["gerrit", "--internal", "label-cq", "123", "1"],
            check=True,
            cwd=chromeos_tree,
            stdin=subprocess.DEVNULL,
        )

    @mock.patch.object(subprocess, "run")
    def test_gerrit_cq_dry_run_only_runs_one_command_if_necessary(
        self, mock_run
    ):
        chromeos_tree = self.make_tempdir()
        update_packages_and_run_tests.cq_dry_run_cls(
            chromeos_tree,
            update_packages_and_run_tests.UploadedCLs(
                internal=[123],
                external=[],
            ),
        )
        mock_run.assert_called_once()
