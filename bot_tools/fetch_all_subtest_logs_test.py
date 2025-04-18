# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for fetch_all_subtest_logs.py."""

from pathlib import Path
import subprocess
import unittest
from unittest import mock

# Rename this, since its original name (& function names) lead to lines that're
# too long.
from bot_tools import fetch_all_subtest_logs as main


class Test(unittest.TestCase):
    """Tests for fetch_all_subtest_logs."""

    @mock.patch.object(subprocess, "run")
    def test_log_downloading_doesnt_crash(self, subprocess_run_mock):
        main.download_gs_logs_to(
            Path("/path/does/not/exist"), gs_logs=["gs://foo", "gs://bar"]
        )
        subprocess_run_mock.assert_called_once()

    @mock.patch.object(main, "get_bb_json_output")
    def test_find_cros_test_platform_raises_if_none_found(
        self, get_bb_json_output_mock
    ):
        get_bb_json_output_mock.return_value = {"steps": []}
        with self.assertRaisesRegex(ValueError, "No `check test results`.*"):
            main.find_cros_test_platform_child_of_cq_orchestrator(
                cq_orchestrator_id=1
            )

    @mock.patch.object(main, "get_bb_json_output")
    def test_find_cros_test_platform_works(self, get_bb_json_output_mock):
        summary_md = "[foo](https://cr-buildbucket.appspot.com/build/123)"
        get_bb_json_output_mock.return_value = {
            "steps": [
                {
                    "name": "foo",
                },
                {
                    "name": "check test results",
                    "summaryMarkdown": summary_md,
                },
            ]
        }
        result = main.find_cros_test_platform_child_of_cq_orchestrator(
            cq_orchestrator_id=1
        )
        self.assertEqual(result, 123)

    def test_gs_link_finding_fails_if_none_found(self):
        with self.assertRaisesRegex(ValueError, "No gs_urls.*"):
            main.find_gs_links_in_test_log({})

    def test_gs_link_finding_finds_links(self):
        results = main.find_gs_links_in_test_log(
            {
                "suite-name-1": [
                    {
                        "Results": {
                            "log_data": {
                                "gs_url": "gs_url_1",
                            }
                        }
                    },
                    {},
                    {"Results": {}},
                    {"Results": {"log_data": {}}},
                ],
                "suite-name-2": [
                    {
                        "Results": {
                            "log_data": {
                                "gs_url": "gs_url_2",
                            }
                        }
                    },
                ],
            }
        )
        self.assertEqual(results, ["gs_url_1", "gs_url_2"])
