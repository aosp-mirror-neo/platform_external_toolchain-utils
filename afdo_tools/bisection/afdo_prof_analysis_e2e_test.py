# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""End-to-end test for afdo_prof_analysis."""

import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile

from afdo_tools.bisection import afdo_prof_analysis as analysis
from llvm_tools import test_helpers


class ObjectWithFields:
    """Turns kwargs given to the constructor into fields on an object.

    Examples:
        x = ObjectWithFields(a=1, b=2)
        assert x.a == 1
        assert x.b == 2
    """

    def __init__(self, **kwargs):
        for key, val in kwargs.items():
            setattr(self, key, val)


class AfdoProfAnalysisE2ETest(test_helpers.TempDirTestCase):
    """Class for end-to-end testing of AFDO Profile Analysis"""

    # nothing significant about the values, just easier to remember even vs odd
    good_prof = {
        "func_a": ":1\n 1: 3\n 3: 5\n 5: 7\n",
        "func_b": ":3\n 3: 5\n 5: 7\n 7: 9\n",
        "func_c": ":5\n 5: 7\n 7: 9\n 9: 11\n",
        "func_d": ":7\n 7: 9\n 9: 11\n 11: 13\n",
        "good_func_a": ":11\n",
        "good_func_b": ":13\n",
    }

    bad_prof = {
        "func_a": ":2\n 2: 4\n 4: 6\n 6: 8\n",
        "func_b": ":4\n 4: 6\n 6: 8\n 8: 10\n",
        "func_c": ":6\n 6: 8\n 8: 10\n 10: 12\n",
        "func_d": ":8\n 8: 10\n 10: 12\n 12: 14\n",
        "bad_func_a": ":12\n",
        "bad_func_b": ":14\n",
    }

    expected = {
        "good_only_functions": False,
        "bad_only_functions": True,
        "bisect_results": {"ranges": [], "individuals": ["func_a"]},
    }

    def setUp(self):
        super().setUp()

        # Test scripts depend on AFDO_TEST_DIR pointing to a directory to run
        # in. Set that up for them.
        self.tempdir = self.make_tempdir()

        saved_value = None
        tmpdir_env_var = "AFDO_TEST_DIR"
        saved_value = os.environ.get(tmpdir_env_var)
        os.environ[tmpdir_env_var] = str(self.tempdir)

        def restore_environ():
            if saved_value is None:
                del os.environ[tmpdir_env_var]
            else:
                os.environ[tmpdir_env_var] = saved_value

        self.addCleanup(restore_environ)

    def test_afdo_prof_analysis(self):
        # Individual issues take precedence by nature of our algos
        # so first, that should be caught
        good = self.good_prof.copy()
        bad = self.bad_prof.copy()
        self.run_check(good, bad, self.expected)

        # Now remove individuals and exclusively BAD, and check that range is
        # caught
        bad["func_a"] = good["func_a"]
        bad.pop("bad_func_a")
        bad.pop("bad_func_b")

        expected_cp = self.expected.copy()
        expected_cp["bad_only_functions"] = False
        expected_cp["bisect_results"] = {
            "individuals": [],
            "ranges": [["func_b", "func_c", "func_d"]],
        }

        self.run_check(good, bad, expected_cp)

    def test_afdo_prof_state(self):
        """Verifies that saved state is correct replication."""
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)

        good = self.good_prof.copy()
        bad = self.bad_prof.copy()
        # add more functions to data
        for x in range(400):
            good["func_%d" % x] = ""
            bad["func_%d" % x] = ""

        fd_first, first_result = tempfile.mkstemp(dir=temp_dir)
        os.close(fd_first)
        fd_state, state_file = tempfile.mkstemp(dir=temp_dir)
        os.close(fd_state)
        self.run_check(
            self.good_prof,
            self.bad_prof,
            self.expected,
            state_file=state_file,
            out_file=first_result,
        )

        fd_second, second_result = tempfile.mkstemp(dir=temp_dir)
        os.close(fd_second)
        completed_state_file = "%s.completed.%s" % (
            state_file,
            str(datetime.date.today()),
        )
        self.run_check(
            self.good_prof,
            self.bad_prof,
            self.expected,
            state_file=completed_state_file,
            no_resume=False,
            out_file=second_result,
        )

        with open(first_result, encoding="utf-8") as f:
            initial_run = json.load(f)
        with open(second_result, encoding="utf-8") as f:
            loaded_run = json.load(f)
        self.assertEqual(initial_run, loaded_run)

    def test_exit_on_problem_status(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)

        fd_state, state_file = tempfile.mkstemp(dir=temp_dir)
        os.close(fd_state)
        with self.assertRaises(RuntimeError):
            self.run_check(
                self.good_prof,
                self.bad_prof,
                self.expected,
                state_file=state_file,
                extern_decider="problemstatus_external.sh",
            )

    def test_state_assumption(self):
        def compare_runs(
            tmp_dir: Path, first_ctr: int, second_ctr: int
        ) -> None:
            """Compares given prof versions between 1st and 2nd run in test."""
            first_prof = tmp_dir / f".first_run_{first_ctr}"
            second_prof = tmp_dir / f".second_run_{second_ctr}"
            first_prof_text = first_prof.read_text(encoding="utf-8")
            second_prof_text = second_prof.read_text(encoding="utf-8")
            self.assertEqual(first_prof_text, second_prof_text)

        good_prof = {"func_a": ":1\n3: 3\n5: 7\n"}
        bad_prof = {"func_a": ":2\n4: 4\n6: 8\n"}
        # add some noise to the profiles; 15 is an arbitrary choice
        for x in range(15):
            func = "func_%d" % x
            good_prof[func] = ":%d\n" % (x)
            bad_prof[func] = ":%d\n" % (x + 1)
        expected = {
            "bisect_results": {"ranges": [], "individuals": ["func_a"]},
            "good_only_functions": False,
            "bad_only_functions": False,
        }

        my_dir = os.path.dirname(os.path.abspath(__file__))
        scripts_tmp_dir = self.tempdir / "afdo_test_tmp"
        scripts_tmp_dir.mkdir()

        # files used in the bash scripts used as external deciders below
        # - count_file tracks the current number of calls to the script in
        #   total
        # - local_count_file tracks the number of calls to the script without
        #   interruption
        count_file = scripts_tmp_dir / ".count"
        local_count_file = scripts_tmp_dir / ".local_count"

        # runs through whole thing at once
        initial_seed = self.run_check(
            good_prof,
            bad_prof,
            expected,
            extern_decider=os.path.join(my_dir, "state_assumption_external.sh"),
        )
        num_calls = int(count_file.read_text(encoding="utf-8"))
        count_file.unlink()

        # runs the same analysis but interrupted each iteration
        interrupt_decider = os.path.join(
            my_dir, "state_assumption_interrupt.sh"
        )
        for i in range(2 * num_calls + 1):
            no_resume_run = i == 0
            seed = initial_seed if no_resume_run else None
            try:
                self.run_check(
                    good_prof,
                    bad_prof,
                    expected,
                    no_resume=no_resume_run,
                    extern_decider=interrupt_decider,
                    seed=seed,
                )
                break
            except RuntimeError:
                # script was interrupted, so we restart local count
                local_count_file.unlink()
        else:
            raise RuntimeError("Test failed -- took too many iterations")

        for initial_ctr in range(3):  # initial runs unaffected by interruption
            compare_runs(scripts_tmp_dir, initial_ctr, initial_ctr)

        start = 3
        for ctr in range(start, num_calls):
            # second run counter incremented by 4 for each one first run is
            # because
            # +2 for performing initial checks on good and bad profs each time
            # +1 for PROBLEM_STATUS run which causes error and restart
            compare_runs(scripts_tmp_dir, ctr, 6 + (ctr - start) * 4)

    def run_check(
        self,
        good_prof,
        bad_prof,
        expected,
        state_file=None,
        no_resume=True,
        out_file=None,
        extern_decider=None,
        seed=None,
    ):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)

        good_prof_file = os.path.join(temp_dir, "good_prof.txt")
        bad_prof_file = os.path.join(temp_dir, "bad_prof.txt")
        good_prof_text = analysis.json_to_text(good_prof)
        bad_prof_text = analysis.json_to_text(bad_prof)
        with open(good_prof_file, "w", encoding="utf-8") as f:
            f.write(good_prof_text)
        with open(bad_prof_file, "w", encoding="utf-8") as f:
            f.write(bad_prof_text)

        dir_path = os.path.dirname(
            os.path.realpath(__file__)
        )  # dir of this file
        external_script = os.path.join(
            dir_path,
            extern_decider or "e2e_external.sh",
        )

        # FIXME: This test ideally shouldn't be writing to the directory of
        # this file.
        if state_file is None:
            state_file = os.path.join(self.tempdir, "afdo_analysis_state.json")

        actual = analysis.main_impl(
            ObjectWithFields(
                good_prof=good_prof_file,
                bad_prof=bad_prof_file,
                external_decider=external_script,
                analysis_output_file=out_file or "/dev/null",
                state_file=state_file,
                no_resume=no_resume,
                remove_state_on_completion=False,
                seed=seed,
            )
        )
        actual_seed = actual.pop("seed")  # nothing to check
        self.assertEqual(actual, expected)
        return actual_seed
