# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs tests for the given input files.

All tests are run in parallel.
"""

# NOTE: An alternative mentioned on the initial CL for this
# https://chromium-review.googlesource.com/c/chromiumos/third_party/toolchain-utils/+/1516414
# is pytest. It looks like that brings some complexity (and makes use outside
# of the chroot a bit more obnoxious?), but might be worth exploring if this
# starts to grow quite complex on its own.


import argparse
import collections
import multiprocessing.pool
import os
import shlex
import signal
import subprocess
import sys
from typing import Optional, Tuple


TestSpec = collections.namedtuple("TestSpec", ["directory", "command"])


def _make_relative_to_toolchain_utils(toolchain_utils, path):
    """Cleans & makes a path relative to toolchain_utils.

    Raises if that path isn't under toolchain_utils.
    """
    # abspath has the nice property that it removes any markers like './'.
    as_abs = os.path.abspath(path)
    result = os.path.relpath(as_abs, start=toolchain_utils)

    if result.startswith("../"):
        raise ValueError("Non toolchain-utils directory found: %s" % result)
    return result


def _run_test(test_spec: TestSpec, timeout: int) -> Tuple[Optional[int], str]:
    """Runs a test.

    Returns a tuple indicating the process' exit code, and the combined
    stdout+stderr of the process. If the exit code is None, the process timed
    out.
    """
    # Each subprocess gets its own process group, since many of these tests
    # spawn subprocesses for a variety of reasons. If these tests time out, we
    # want to be able to clean up all of the children swiftly.
    # pylint: disable=subprocess-popen-preexec-fn
    with subprocess.Popen(
        test_spec.command,
        cwd=test_spec.directory,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        # TODO(b/296616854): This is unsafe, and we should use
        # process_group=0 when we have upgraded to Python 3.11.
        preexec_fn=lambda: os.setpgid(0, 0),
    ) as p:
        child_pgid = p.pid
        try:
            out, _ = p.communicate(timeout=timeout)
            return p.returncode, out
        except BaseException as e:
            # Try to shut the processes down gracefully.
            os.killpg(child_pgid, signal.SIGINT)
            try:
                # 2 seconds is arbitrary, but given that these are unittests,
                # should be plenty of time for them to shut down.
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(child_pgid, signal.SIGKILL)
            except:
                os.killpg(child_pgid, signal.SIGKILL)
                raise

            if isinstance(e, subprocess.TimeoutExpired):
                # We just killed the entire process group. This should complete
                # ~immediately. If it doesn't, something is very wrong.
                out, _ = p.communicate(timeout=5)
                return (None, out)
            raise


def _run_test_scripts(pool, all_tests, timeout, show_successful_output=False):
    """Runs a list of TestSpecs. Returns whether all of them succeeded."""
    results = [
        pool.apply_async(_run_test, (test, timeout)) for test in all_tests
    ]

    failures = []
    for i, (test, future) in enumerate(zip(all_tests, results)):
        # Add a bit more spacing between outputs.
        if show_successful_output and i:
            print("\n")

        pretty_test = shlex.join(test.command)
        pretty_directory = os.path.relpath(test.directory)
        if pretty_directory == ".":
            test_message = pretty_test
        else:
            test_message = "%s in %s/" % (pretty_test, pretty_directory)

        print("## %s ... " % test_message, end="")
        # Be sure that the users sees which test is running.
        sys.stdout.flush()

        exit_code, stdout = future.get()
        if exit_code == 0:
            print("PASS")
            is_failure = False
        else:
            print("TIMEOUT" if exit_code is None else "FAIL")
            failures.append(test_message)
            is_failure = True

        if show_successful_output or is_failure:
            if stdout:
                print("-- Stdout:\n", stdout)
            else:
                print("-- No stdout was produced.")

    if failures:
        word = "tests" if len(failures) > 1 else "test"
        print(f"{len(failures)} {word} failed:")
        for failure in failures:
            print(f"\t{failure}")

    return not failures


def _compress_list(l):
    """Removes consecutive duplicate elements from |l|.

    >>> _compress_list([])
    []
    >>> _compress_list([1, 1])
    [1]
    >>> _compress_list([1, 2, 1])
    [1, 2, 1]
    """
    result = []
    for e in l:
        if result and result[-1] == e:
            continue
        result.append(e)
    return result


def _find_go_tests(test_paths):
    """Returns TestSpecs for the go folders of the given files"""
    assert all(os.path.isabs(path) for path in test_paths)

    dirs_with_gofiles = set(
        os.path.dirname(p) for p in test_paths if p.endswith(".go")
    )
    command = ("go", "test", "-vet=all")
    # Note: We sort the directories to be deterministic.
    return [
        TestSpec(directory=d, command=command)
        for d in sorted(dirs_with_gofiles)
    ]


def main(argv):
    default_toolchain_utils = os.path.abspath(os.path.dirname(__file__))

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show_all_output",
        action="store_true",
        help="show stdout of successful tests",
    )
    parser.add_argument(
        "--toolchain_utils",
        default=default_toolchain_utils,
        help="directory of toolchain-utils. Often auto-detected",
    )
    parser.add_argument(
        "file", nargs="*", help="a file that we should run tests for"
    )
    parser.add_argument(
        "--timeout",
        default=120,
        type=int,
        help="Time to allow a test to execute before timing it out, in "
        "seconds.",
    )
    args = parser.parse_args(argv)

    modified_files = [os.path.abspath(f) for f in args.file]
    show_all_output = args.show_all_output
    toolchain_utils = args.toolchain_utils

    if not modified_files:
        print("No files given. Exit.")
        return 0

    tests_to_run = []
    if any(x.endswith(".py") for x in modified_files):
        tests_to_run.append(
            TestSpec(
                directory=toolchain_utils,
                command=("./run_python_tests.sh",),
            )
        )

    tests_to_run += _find_go_tests(modified_files)

    # TestSpecs have lists, so we can't use a set. We'd likely want to keep them
    # sorted for determinism anyway.
    tests_to_run = sorted(set(tests_to_run))

    with multiprocessing.pool.ThreadPool() as pool:
        success = _run_test_scripts(
            pool, tests_to_run, args.timeout, show_all_output
        )
    return 0 if success else 1
