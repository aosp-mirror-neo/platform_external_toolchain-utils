# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs presubmit checks against a bundle of files."""

import argparse
import dataclasses
import datetime
import functools
import multiprocessing
import multiprocessing.pool
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import threading
import traceback
from typing import Callable, Iterable, NamedTuple, Sequence

from cros_utils import cros_paths


# Each checker represents an independent check that's done on our sources.
#
# They should:
#  - never write to stdout/stderr or read from stdin directly
#  - return either a CheckResult, or a list of [(subcheck_name, CheckResult)]
#  - ideally use thread_pool to check things concurrently
#    - though it's important to note that these *also* live on the threadpool
#      we've provided. It's the caller's responsibility to guarantee that at
#      least ${number_of_concurrently_running_checkers}+1 threads are present
#      in the pool. In order words, blocking on results from the provided
#      threadpool is OK.
CheckResult = NamedTuple(
    "CheckResult",
    (
        ("ok", bool),
        ("output", str),
        ("autofix_commands", list[list[str]]),
    ),
)


Command = Sequence[str | os.PathLike]
CheckResults = list[tuple[str, CheckResult]] | CheckResult


# Environment variable that's set to a nonempty value on bots. Used for
# skipping some tasks on CI. Other presubmit checks detect whether a bot is
# running the check in a similar way.
SWARMING_TASK_ID_ENV = "SWARMING_TASK_ID"

# Environment variables to forward to the `cros_sdk` invocation, if we're
# re-execing in the chroot.
CHROOT_FORWARDED_ENV = (SWARMING_TASK_ID_ENV,)

# (str) paths relative to toolchain-utils' root where `__name__ == "__main__"`
# is allowed. Generally speaking, these don't work as one might expect, due to
# how we use wrappers.
NAME_MAIN_ALLOWLIST = (
    # This is just mirrored from the compiler wrapper dir, so is also directly
    # executed by users.
    "compiler_wrapper/build.py",
    # This is mirrored from LLVM's upstream; no point in having divergence.
    "llvm_tools/revert_checker.py",
    # These are directly executed by users.
    "venv_python3_wrapper.py",
    "venvless_python3_wrapper.py",
    "venv_tc/wheels.py",
)


def run_command_unchecked(
    command: Command,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> tuple[int, str, bool]:
    """Runs a command in the given dir, returning its exit code and stdio.

    Returns:
        A tuple of (exit_code, output, timed_out).
    """
    try:
        p = subprocess.run(
            command,
            check=False,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return p.returncode, p.stdout, False
    except subprocess.TimeoutExpired as e:
        return -1, default_timeout_message(command, e), True


def default_timeout_message(
    command: Command, ex: subprocess.TimeoutExpired
) -> str:
    """Returns a default message for command timeouts."""
    msg = (
        f"Command `{shlex.join(str(x) for x in command)}` "
        f"timed out after {ex.timeout} seconds"
    )
    if ex.output:
        msg += f"\nStdstreams:\n{ex.output.decode('utf-8', errors='replace')}"
    return msg


def has_executable_on_path(exe: str) -> bool:
    """Returns whether we have `exe` somewhere on our $PATH"""
    return shutil.which(exe) is not None


def remove_deleted_files(files: Iterable[str]) -> list[str]:
    return [f for f in files if os.path.exists(f)]


def is_file_executable(file_path: str) -> bool:
    return os.access(file_path, os.X_OK)


# As noted in our docs, some of our Python code depends on modules that sit in
# toolchain-utils/. Add that to PYTHONPATH to ensure that things like `cros
# lint` are kept happy.
def env_with_pythonpath(toolchain_utils_root: str) -> dict[str, str]:
    env = dict(os.environ)
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] += ":" + toolchain_utils_root
    else:
        env["PYTHONPATH"] = toolchain_utils_root
    return env


@dataclasses.dataclass(frozen=True)
class MyPyInvocation:
    """An invocation of mypy."""

    command: list[str]


def get_mypy() -> MyPyInvocation:
    """Returns a MyPyInvocation that can be used to run mypy."""
    return MyPyInvocation(
        command=[
            sys.executable,
            "-m",
            "mypy",
        ],
    )


def get_check_result_or_catch(
    task: multiprocessing.pool.ApplyResult,
) -> CheckResult:
    """Returns the result of task(); if that raises, returns a CheckResult.

    The task is expected to return a CheckResult on get().
    """
    try:
        return task.get()
    except Exception:
        return CheckResult(
            ok=False,
            output="Check exited with an unexpected exception:\n%s"
            % traceback.format_exc(),
            autofix_commands=[],
        )


def check_isort(
    toolchain_utils_root: str, python_files: Iterable[str]
) -> CheckResult:
    """Subchecker of check_py_format. Checks python file formats with isort"""
    chromite = Path("/mnt/host/source/chromite")
    isort = chromite / "scripts" / "isort"
    config_file = chromite / ".isort.cfg"

    if not (isort.exists() and config_file.exists()):
        return CheckResult(
            ok=True,
            output="isort not found; skipping",
            autofix_commands=[],
        )

    config_file_flag = f"--settings-file={config_file}"
    command = [str(isort), "-c", config_file_flag] + list(python_files)
    exit_code, stdout_and_stderr, timed_out = run_command_unchecked(
        command, cwd=toolchain_utils_root
    )
    if timed_out:
        return CheckResult(
            ok=False, output=stdout_and_stderr, autofix_commands=[]
        )

    # isort fails when files have broken formatting.
    if not exit_code:
        return CheckResult(
            ok=True,
            output="",
            autofix_commands=[],
        )

    bad_files = []
    bad_file_re = re.compile(
        r"^ERROR: (.*) Imports are incorrectly sorted and/or formatted\.$"
    )
    for line in stdout_and_stderr.splitlines():
        m = bad_file_re.match(line)
        if m:
            (file_name,) = m.groups()
            bad_files.append(file_name.strip())

    if not bad_files:
        return CheckResult(
            ok=False,
            output=f"`{shlex.join(command)}` failed; stdout/stderr:\n"
            f"{stdout_and_stderr}",
            autofix_commands=[],
        )

    autofix = [str(isort), config_file_flag] + bad_files
    return CheckResult(
        ok=False,
        output="The following file(s) have formatting errors: %s" % bad_files,
        autofix_commands=[autofix],
    )


def check_black(
    toolchain_utils_root: str, black: Path, python_files: Iterable[str]
) -> CheckResult:
    """Subchecker of check_py_format. Checks python file formats with black"""
    # Folks have been bitten by accidentally using multiple formatter
    # versions in the past. This is an issue, since newer versions of
    # black may format things differently. Make the version obvious.
    command: Command = [black, "--version"]
    exit_code, stdout_and_stderr, timed_out = run_command_unchecked(
        command, cwd=toolchain_utils_root
    )
    if timed_out:
        return CheckResult(
            ok=False, output=stdout_and_stderr, autofix_commands=[]
        )
    if exit_code:
        return CheckResult(
            ok=False,
            output="Failed getting black version; "
            f"stdstreams: {stdout_and_stderr}",
            autofix_commands=[],
        )

    black_version = stdout_and_stderr.strip()
    black_invocation: list[str] = [str(black), "--line-length=80"]
    command = black_invocation + ["--check"] + list(python_files)
    exit_code, stdout_and_stderr, timed_out = run_command_unchecked(
        command, cwd=toolchain_utils_root
    )
    if timed_out:
        return CheckResult(
            ok=False, output=stdout_and_stderr, autofix_commands=[]
        )
    # black fails when files are poorly formatted.
    if exit_code == 0:
        return CheckResult(
            ok=True,
            output=f"Using {black_version!r}, no issues were found.",
            autofix_commands=[],
        )

    # Output format looks something like:
    # f'{complaints}\nOh no!{emojis}\n{summary}'
    # Whittle it down to complaints.
    complaints = stdout_and_stderr.split("\nOh no!", 1)
    if len(complaints) != 2:
        return CheckResult(
            ok=False,
            output=f"Unparseable `black` output:\n{stdout_and_stderr}",
            autofix_commands=[],
        )

    bad_files = []
    errors = []
    refmt_prefix = "would reformat "
    for line in complaints[0].strip().splitlines():
        line = line.strip()
        if line.startswith("error:"):
            errors.append(line)
            continue

        if not line.startswith(refmt_prefix):
            return CheckResult(
                ok=False,
                output=f"Unparseable `black` output:\n{stdout_and_stderr}",
                autofix_commands=[],
            )

        bad_files.append(line[len(refmt_prefix) :].strip())

    # If black had internal errors that it could handle, print them out and exit
    # without an autofix.
    if errors:
        err_str = "\n".join(errors)
        return CheckResult(
            ok=False,
            output=f"Using {black_version!r} had the following errors:\n"
            f"{err_str}",
            autofix_commands=[],
        )

    autofix = black_invocation + bad_files
    return CheckResult(
        ok=False,
        output=f"Using {black_version!r}, these file(s) have formatting "
        f"errors: {bad_files}",
        autofix_commands=[autofix],
    )


def check_mypy(
    toolchain_utils_root: str,
    mypy: MyPyInvocation,
    files: Iterable[str],
) -> CheckResult:
    """Checks type annotations using mypy."""
    fixed_env = env_with_pythonpath(toolchain_utils_root)
    # Show the version number, mainly for troubleshooting purposes.
    cmd = mypy.command + ["--version"]
    exit_code, output, timed_out = run_command_unchecked(
        cmd, cwd=toolchain_utils_root, env=fixed_env
    )
    if timed_out:
        return CheckResult(ok=False, output=output, autofix_commands=[])
    if exit_code:
        return CheckResult(
            ok=False,
            output=f"Failed getting mypy version; stdstreams: {output}",
            autofix_commands=[],
        )
    # Prefix output with the version information.
    prefix = f"Using {output.strip()}, "

    cmd = list(mypy.command)
    cmd += files
    exit_code, output, timed_out = run_command_unchecked(
        cmd, cwd=toolchain_utils_root, env=fixed_env
    )
    if timed_out:
        return CheckResult(ok=False, output=output, autofix_commands=[])
    if exit_code == 0:
        return CheckResult(
            ok=True,
            output=f"{output}{prefix}checks passed",
            autofix_commands=[],
        )
    else:
        return CheckResult(
            ok=False,
            output=f"{output}{prefix}type errors were found",
            autofix_commands=[],
        )


def check_python_file_headers(python_files: Iterable[str]) -> CheckResult:
    """Subchecker of check_py_format. Checks python #!s"""
    add_hashbang = []
    remove_hashbang = []

    for python_file in python_files:
        needs_hashbang = is_file_executable(python_file)
        with open(python_file, encoding="utf-8") as f:
            has_hashbang = f.read(2) == "#!"
            if needs_hashbang == has_hashbang:
                continue

            if needs_hashbang:
                add_hashbang.append(python_file)
            else:
                remove_hashbang.append(python_file)

    autofix = []
    output = []
    if add_hashbang:
        output.append(
            "The following files have no #!, but need one: %s" % add_hashbang
        )
        autofix.append(["sed", "-i", "1i#!/usr/bin/env python3"] + add_hashbang)

    if remove_hashbang:
        output.append(
            "The following files have a #!, but shouldn't: %s" % remove_hashbang
        )
        autofix.append(["sed", "-i", "1d"] + remove_hashbang)

    if not output:
        return CheckResult(
            ok=True,
            output="",
            autofix_commands=[],
        )
    return CheckResult(
        ok=False,
        output="\n".join(output),
        autofix_commands=autofix,
    )


def check_python_name_eq_main(
    toolchain_utils_root: str, python_files: Iterable[str]
) -> CheckResult:
    """Subchecker of check_py_format. Checks for no __name__ == __main__."""
    name_main_re = re.compile(r"if\s+__name__\s+==\s+['\"]__main__['\"]:")
    bad_files = []
    abs_name_main_allowlist = {
        Path(toolchain_utils_root, x) for x in NAME_MAIN_ALLOWLIST
    }
    bad_files = [
        x
        for x in (Path(x) for x in python_files)
        if x not in abs_name_main_allowlist
        and name_main_re.search(x.read_text(encoding="utf-8"))
    ]

    if not bad_files:
        return CheckResult(
            ok=True,
            output="",
            autofix_commands=[],
        )

    error_lines = [
        "if __name__ == '__main__' detected in unexpected files. This does ",
        "nothing most of the time due to our Python wrappers. Please remove ",
        "it, or add the file to NAME_MAIN_ALLOWLIST in check-presubmit.py.",
        "",
        "File(s):",
    ]
    error_lines += (f"- {x}" for x in bad_files)
    return CheckResult(
        ok=False,
        output="\n".join(error_lines),
        # It's kind of difficult to autofix this, since we need to scrape for
        # multiple `__name__ == "__main__"` blocks and everything indented under
        # them. Should be simple enough for the user to handle themself.
        autofix_commands=[],
    )


def check_py_format(
    toolchain_utils_root: str,
    thread_pool: multiprocessing.pool.ThreadPool,
    files: Iterable[str],
) -> CheckResults:
    """Runs black on files to check for style bugs. Also checks for #!s."""
    # Prefer the `black` that ships with chromite first, as that's properly
    # version-controlled.
    black = os.path.normpath(
        os.path.join(toolchain_utils_root, "../../../chromite/scripts/black")
    )
    if not os.path.exists(black):
        black = "black"
        if not has_executable_on_path(black):
            return CheckResult(
                ok=False,
                output="black isn't available on your $PATH. Please either "
                "enter a chroot, or place depot_tools on your $PATH.",
                autofix_commands=[],
            )

    python_files = [f for f in remove_deleted_files(files) if f.endswith(".py")]
    if not python_files:
        return CheckResult(
            ok=True,
            output="no python files to check",
            autofix_commands=[],
        )

    tasks = [
        (
            "check_black",
            thread_pool.apply_async(
                check_black, (toolchain_utils_root, black, python_files)
            ),
        ),
        (
            "check_isort",
            thread_pool.apply_async(
                check_isort, (toolchain_utils_root, python_files)
            ),
        ),
        (
            "check_file_headers",
            thread_pool.apply_async(check_python_file_headers, (python_files,)),
        ),
        (
            "check_name_eq_main",
            thread_pool.apply_async(
                check_python_name_eq_main, (toolchain_utils_root, python_files)
            ),
        ),
    ]
    return [(name, get_check_result_or_catch(task)) for name, task in tasks]


def check_py_types(
    mypy: MyPyInvocation,
    toolchain_utils_root: str,
    thread_pool: multiprocessing.pool.ThreadPool,
    files: Iterable[str],
) -> CheckResults:
    """Runs static type checking for Python files."""
    to_check = [x for x in files if x.endswith(".py")]
    if not to_check:
        return CheckResult(
            ok=True,
            output="no python files to typecheck",
            autofix_commands=[],
        )

    tasks = [
        (
            "check_mypy",
            thread_pool.apply_async(
                check_mypy, (toolchain_utils_root, mypy, to_check)
            ),
        ),
    ]
    return [(name, get_check_result_or_catch(task)) for name, task in tasks]


def find_chromeos_root_directory() -> str | None:
    return os.getenv("CHROMEOS_ROOT_DIRECTORY")


def check_cros_lint(
    toolchain_utils_root: str,
    thread_pool: multiprocessing.pool.ThreadPool,
    files: Iterable[str],
) -> CheckResults:
    """Runs `cros lint`"""

    fixed_env = env_with_pythonpath(toolchain_utils_root)

    # b/404578092: if `cros lint` is given absolute paths, it may skip
    # linting for reasons that are unclear as of the time of writing.
    # Just give it relative paths, since it's already invoked
    # from toolchain_utils_root.
    fixed_files = [os.path.relpath(x, toolchain_utils_root) for x in files]

    # We have to support users who don't have a chroot. So we either run `cros
    # lint` (if it's been made available to us), or we try a mix of
    # pylint+staticcheck.
    def try_run_cros_lint(cros_binary: str) -> CheckResult | None:
        cmd = [cros_binary, "lint", "--"] + fixed_files
        exit_code, output, timed_out = run_command_unchecked(
            cmd, cwd=toolchain_utils_root, env=fixed_env
        )
        if timed_out:
            return CheckResult(ok=False, output=output, autofix_commands=[])

        # This is returned specifically if cros couldn't find the ChromeOS tree
        # root.
        if exit_code == 127:
            return None

        return CheckResult(
            ok=exit_code == 0,
            output=output,
            autofix_commands=[],
        )

    cros_lint = try_run_cros_lint("cros")
    if cros_lint is not None:
        return cros_lint

    cros_root = find_chromeos_root_directory()
    if cros_root:
        cros_lint = try_run_cros_lint(
            os.path.join(cros_root, "chromite/bin/cros")
        )
        if cros_lint is not None:
            return cros_lint

    tasks = []

    def check_result_from_command(command: list[str]) -> CheckResult:
        exit_code, output, timed_out = run_command_unchecked(
            command, cwd=toolchain_utils_root, env=fixed_env
        )
        if timed_out:
            return CheckResult(ok=False, output=output, autofix_commands=[])
        return CheckResult(
            ok=exit_code == 0,
            output=output,
            autofix_commands=[],
        )

    python_files = [f for f in remove_deleted_files(files) if f.endswith(".py")]
    if python_files:

        def run_pylint() -> CheckResult:
            # pylint is required. Fail hard if it DNE.
            return check_result_from_command(["pylint"] + python_files)

        tasks.append(("pylint", thread_pool.apply_async(run_pylint)))

    go_files = [f for f in remove_deleted_files(files) if f.endswith(".go")]
    if go_files:

        def run_staticcheck() -> CheckResult:
            if has_executable_on_path("staticcheck"):
                return check_result_from_command(
                    ["staticcheck", "-checks", "inherit,-SA1019"] + go_files
                )

            complaint = (
                "WARNING: go linting disabled. staticcheck is not on your "
                "$PATH.\nPlease either enter a chroot, or install go locally. "
                "Continuing."
            )
            return CheckResult(
                ok=True,
                output=complaint,
                autofix_commands=[],
            )

        tasks.append(("staticcheck", thread_pool.apply_async(run_staticcheck)))

    complaint = (
        "WARNING: No ChromeOS checkout detected, and no viable CrOS tree\n"
        "found; falling back to linting only python and go. If you have a\n"
        "ChromeOS checkout, please either develop from inside of the source\n"
        "tree, or set $CHROMEOS_ROOT_DIRECTORY to the root of it."
    )

    results = [(name, get_check_result_or_catch(task)) for name, task in tasks]
    if not results:
        return CheckResult(
            ok=True,
            output=complaint,
            autofix_commands=[],
        )

    # We need to complain _somewhere_.
    name, angry_result = results[0]
    angry_complaint = (complaint + "\n\n" + angry_result.output).strip()
    results[0] = (name, angry_result._replace(output=angry_complaint))
    return results


def check_go_format(
    toolchain_utils_root: str,
    _thread_pool: multiprocessing.pool.ThreadPool,
    files: Iterable[str],
) -> CheckResult:
    """Runs gofmt on files to check for style bugs."""
    gofmt = "gofmt"
    if not has_executable_on_path(gofmt):
        return CheckResult(
            ok=False,
            output="gofmt isn't available on your $PATH. Please either "
            "enter a chroot, or place your go bin/ directory on your $PATH.",
            autofix_commands=[],
        )

    go_files = [f for f in remove_deleted_files(files) if f.endswith(".go")]
    if not go_files:
        return CheckResult(
            ok=True,
            output="no go files to check",
            autofix_commands=[],
        )

    command = [gofmt, "-l"] + go_files
    exit_code, output, timed_out = run_command_unchecked(
        command, cwd=toolchain_utils_root
    )
    if timed_out:
        return CheckResult(ok=False, output=output, autofix_commands=[])

    if exit_code:
        return CheckResult(
            ok=False,
            output=f"{shlex.join(command)} failed; stdout/stderr:\n{output}",
            autofix_commands=[],
        )

    output = output.strip()
    if not output:
        return CheckResult(
            ok=True,
            output="",
            autofix_commands=[],
        )

    broken_files = [x.strip() for x in output.splitlines()]
    autofix = [gofmt, "-w"] + broken_files
    return CheckResult(
        ok=False,
        output="The following Go files have incorrect "
        "formatting: %s" % broken_files,
        autofix_commands=[autofix],
    )


def check_json_format(
    toolchain_utils_root: str,
    thread_pool: multiprocessing.pool.ThreadPool,
    files: Iterable[str],
) -> CheckResult:
    """Runs `cros format --check` on JSON files."""
    json_files = [f for f in remove_deleted_files(files) if f.endswith(".json")]
    if not json_files:
        return CheckResult(
            ok=True,
            output="no json files to check",
            autofix_commands=[],
        )

    if not has_executable_on_path("cros"):
        complaint = textwrap.dedent(
            """\
            WARNING: JSON formatting check disabled. `cros` is not on your
            $PATH. Please either enter a chroot, or ensure `cros` is available.
            Continuing.
            """
        )
        return CheckResult(
            ok=True,
            output=complaint,
            autofix_commands=[],
        )

    def check_file(file_path: str) -> tuple[CheckResult, bool]:
        """Checks formatting of a JSON file.

        Returns:
            A tuple of (CheckResult, timeout_expired).
        """
        cmd = ("cros", "format", "--check", file_path)
        exit_code, output, timed_out = run_command_unchecked(
            cmd, cwd=toolchain_utils_root
        )
        if timed_out:
            return (
                CheckResult(ok=False, output=output, autofix_commands=[]),
                True,
            )
        return (
            CheckResult(
                ok=exit_code == 0,
                output=file_path if exit_code else "",
                autofix_commands=[],
            ),
            False,
        )

    results = thread_pool.map(check_file, json_files)

    if all(r.ok for r, _ in results):
        return CheckResult(
            ok=True,
            output="all JSON files are properly formatted",
            autofix_commands=[],
        )

    bad_files = [
        r.output for r, timed_out in results if not r.ok and not timed_out
    ]
    timeout_messages = [r.output for r, timed_out in results if timed_out]

    output = []
    if timeout_messages:
        output.extend(timeout_messages)
    if bad_files:
        output.append(
            f"The following JSON files have incorrect formatting: {bad_files}"
        )

    autofix = [["cros", "format"] + bad_files] if bad_files else []
    return CheckResult(
        ok=False,
        output="\n".join(output),
        autofix_commands=autofix,
    )


def is_running_on_bot() -> bool:
    """Returns True if this script is executing on a bot."""
    return bool(os.environ.get(SWARMING_TASK_ID_ENV))


def check_no_compiler_wrapper_changes(
    toolchain_utils_root: str,
    _thread_pool: multiprocessing.pool.ThreadPool,
    files: Iterable[str],
) -> CheckResult:
    if is_running_on_bot():
        return CheckResult(
            ok=True,
            output="Skipping compiler_wrapper change detection on bot",
            autofix_commands=[],
        )

    compiler_wrapper_prefix = (
        os.path.join(toolchain_utils_root, "compiler_wrapper") + "/"
    )
    if not any(x.startswith(compiler_wrapper_prefix) for x in files):
        return CheckResult(
            ok=True,
            output="No compiler_wrapper changes detected",
            autofix_commands=[],
        )

    return CheckResult(
        ok=False,
        autofix_commands=[],
        output=textwrap.dedent(
            """\
            Compiler wrapper changes should be made in chromiumos-overlay.
            If you're a CrOS toolchain maintainer, please make the change
            directly there now. If you're contributing as part of a downstream
            (e.g., the Android toolchain team), feel free to bypass this check
            and note to your reviewer that you received this message. They can
            review your CL and commit to the right plate for you. Thanks!
            """
        ).strip(),
    )


def check_tests(
    toolchain_utils_root: str,
    _thread_pool: multiprocessing.pool.ThreadPool,
    files: Iterable[str],
) -> CheckResult:
    """Runs tests."""
    run_tests_for = os.path.join(
        toolchain_utils_root,
        cros_paths.TOOLCHAIN_UTILS_PYBIN_REL,
        "run_tests_for",
    )
    cmd = [run_tests_for, "--"]
    cmd += files
    exit_code, stdout_and_stderr, timed_out = run_command_unchecked(
        cmd, cwd=toolchain_utils_root
    )
    if timed_out:
        return CheckResult(
            ok=False, output=stdout_and_stderr, autofix_commands=[]
        )
    return CheckResult(
        ok=exit_code == 0,
        output=stdout_and_stderr,
        autofix_commands=[],
    )


def detect_toolchain_utils_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def process_check_result(
    check_name: str,
    check_results: CheckResults,
    start_time: datetime.datetime,
) -> tuple[bool, list[list[str]]]:
    """Prints human-readable output for the given check_results."""
    indent = "  "

    def indent_block(text: str) -> str:
        return indent + text.replace("\n", "\n" + indent)

    if isinstance(check_results, CheckResult):
        ok, output, autofix_commands = check_results
        if not ok and autofix_commands:
            recommendation = (
                "Recommended command(s) to fix this: "
                f"{[shlex.join(x) for x in autofix_commands]}"
            )
            if output:
                output += "\n" + recommendation
            else:
                output = recommendation
    else:
        output_pieces = []
        autofix_commands = []
        for subname, (ok, output, autofix) in check_results:
            status = "succeeded" if ok else "failed"
            message = ["*** %s.%s %s" % (check_name, subname, status)]
            if output:
                message.append(indent_block(output))
            if not ok and autofix:
                message.append(
                    indent_block(
                        "Recommended command(s) to fix this: "
                        f"{[shlex.join(x) for x in autofix]}"
                    )
                )

            output_pieces.append("\n".join(message))
            autofix_commands += autofix

        ok = all(x.ok for _, x in check_results)
        output = "\n\n".join(output_pieces)

    time_taken = datetime.datetime.now() - start_time
    if ok:
        print("*** %s succeeded after %s" % (check_name, time_taken))
    else:
        print("*** %s failed after %s" % (check_name, time_taken))

    if output:
        print(indent_block(output))

    print()
    return ok, autofix_commands


def try_autofix(
    all_autofix_commands: list[list[str]],
    toolchain_utils_root: str,
    force_autofix: bool,
) -> None:
    """Tries to run all given autofix commands, if appropriate."""
    if not all_autofix_commands:
        return

    if not force_autofix:
        exit_code, output, timed_out = run_command_unchecked(
            ("git", "status", "--porcelain"), cwd=toolchain_utils_root
        )
        if timed_out:
            print(f"Autofix aborted: {output}")
            return
        if exit_code:
            print("Autofix aborted: couldn't get toolchain-utils git status.")
            return

        if output.strip():
            # A clean repo makes checking/undoing autofix commands trivial. A
            # dirty one... less so. :)
            print(
                "Git repo seems dirty; skipping autofix. Rerun with "
                "`--force_autofix` to autofix anyway."
            )
            return

    anything_succeeded = False
    for command in all_autofix_commands:
        exit_code, output, timed_out = run_command_unchecked(
            command, cwd=toolchain_utils_root
        )
        if timed_out:
            print(f"*** {output}")
            continue

        if exit_code:
            print(
                f"*** Autofix command `{shlex.join(command)}` exited with "
                f"code {exit_code}; stdout/stderr:"
            )
            print(output)
        else:
            print(f"*** Autofix `{shlex.join(command)}` succeeded")
            anything_succeeded = True

    if anything_succeeded:
        print(
            "NOTE: Autofixes have been applied. Please check your tree, since "
            "some lints may now be fixed"
        )


def find_repo_root(base_dir: str) -> str | None:
    current = base_dir
    while current != "/":
        if os.path.isdir(os.path.join(current, ".repo")):
            return current
        current = os.path.dirname(current)
    return None


def is_in_chroot() -> bool:
    return os.path.exists("/etc/cros_chroot_version")


def maybe_reexec_inside_chroot(
    autofix_allowed: bool,
    force_autofix: bool,
    infer_files: bool,
    files: list[str],
) -> None:
    if is_in_chroot():
        return

    enter_chroot = True
    chdir_to = None
    toolchain_utils = detect_toolchain_utils_root()
    if find_repo_root(toolchain_utils) is None:
        chromeos_root_dir = find_chromeos_root_directory()
        if chromeos_root_dir is None:
            print(
                "Standalone toolchain-utils checkout detected; cannot enter "
                "chroot."
            )
            enter_chroot = False
        else:
            chdir_to = chromeos_root_dir

    if not has_executable_on_path("cros_sdk"):
        print("No `cros_sdk` detected on $PATH; cannot enter chroot.")
        enter_chroot = False

    if not enter_chroot:
        print(
            "Giving up on entering the chroot; be warned that some presubmits "
            "may be broken."
        )
        return

    # We'll be changing ${PWD}, so make everything relative to toolchain-utils,
    # which resides at a well-known place inside of the chroot.
    chroot_toolchain_utils = "/mnt/host/source/src/third_party/toolchain-utils"

    def rebase_path(path: str) -> str:
        return os.path.join(
            chroot_toolchain_utils, os.path.relpath(path, toolchain_utils)
        )

    args = [
        "cros_sdk",
        "--enter",
    ]

    for env_var in CHROOT_FORWARDED_ENV:
        val = os.environ.get(env_var)
        if val is not None:
            args.append(f"{env_var}={val}")

    args += [
        "--",
        os.path.join(
            chroot_toolchain_utils,
            cros_paths.TOOLCHAIN_UTILS_PYBIN_REL,
            "toolchain_utils_githooks",
            "check-presubmit",
        ),
    ]

    if not autofix_allowed:
        args.append("--no_autofix")
    if force_autofix:
        args.append("--force_autofix")
    if infer_files:
        args.append("--infer_files")
    args.extend(rebase_path(x) for x in files)

    if chdir_to is None:
        print("Attempting to enter the chroot...")
    else:
        print(f"Attempting to enter the chroot for tree at {chdir_to}...")
        os.chdir(chdir_to)
    os.execvp(args[0], args)


def can_import_py_module(module: str) -> bool:
    """Returns true if `import {module}` works."""
    exit_code = subprocess.call(
        ["python3", "-c", f"import {module}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return exit_code == 0


def infer_files_from_env_or_die(toolchain_utils_root: Path) -> list[str]:
    env = os.environ

    # If we have PRESUBMIT_FILES, use those. It's a newline-delimeted list.
    if presubmit_files := env.get("PRESUBMIT_FILES"):
        return [x.strip() for x in presubmit_files.splitlines()]

    # Otherwise, we're probably executing in the context of a
    # fullcheckout-presubmit builder. These commit patches locally, then set up
    # a branch with a properly-init'ed upstream for us. Scrape the diff between
    # HEAD and that to determine what to lint.
    upstream = subprocess.run(
        ["git", "rev-parse", "@{u}"],
        check=False,
        cwd=toolchain_utils_root,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if upstream.returncode:
        raise ValueError(
            "No upstream could be parsed for inference - "
            "make sure you're running on a branch."
        )
    upstream_main = upstream.stdout.strip()

    # On builders, merge-base isn't necessary, but in case a dev is running
    # this locally, this is helpful (e.g., if a dev has `git fetch`ed but not
    # rebased, we don't want the newly-fetched diffs to show up in the git diff
    # output).
    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", upstream_main],
        check=True,
        cwd=toolchain_utils_root,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    diff = subprocess.run(
        ["git", "diff", merge_base],
        check=True,
        cwd=toolchain_utils_root,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if not diff:
        raise ValueError(f"There's no diff between HEAD and {merge_base}.")

    # N.B., if files are only deleted (`+++ /dev/null`), this will have no
    # matches. That's fine.
    return [
        os.path.join(toolchain_utils_root, x)
        for x in re.findall(r"^\+\+\+ b/([^\n]+)$", diff, re.MULTILINE)
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    autofix_group = parser.add_mutually_exclusive_group()
    autofix_group.add_argument(
        "--no_autofix",
        dest="autofix_allowed",
        action="store_false",
        help="Don't run any autofix commands.",
    )
    autofix_group.add_argument(
        "--force_autofix",
        action="store_true",
        help="Run autofix commands even if the tree is dirty.",
    )
    parser.add_argument(
        "--no_enter_chroot",
        dest="enter_chroot",
        action="store_false",
        help="Prevent auto-entering the chroot if we're not already in it.",
    )
    parser.add_argument(
        "--infer_files",
        action="store_true",
        help="""
        If passed, the file list will be inferred from git state and the
        environment. This is mutually exclusive with passing `files`.
        """,
    )
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(argv)

    if bool(opts.files) == opts.infer_files:
        parser.error(
            "Either `--infer_files` or a list of files must be passed, "
            "not both."
        )
    return opts


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    infer_files = opts.infer_files
    files = opts.files

    toolchain_utils_root = detect_toolchain_utils_root()
    if opts.enter_chroot:
        maybe_reexec_inside_chroot(
            opts.autofix_allowed, opts.force_autofix, infer_files, files
        )

    if infer_files:
        files = infer_files_from_env_or_die(Path(toolchain_utils_root))
        print(f"Inferred files to check: {files}")

    mypy = get_mypy()

    # Most checks shouldn't check symlinks - changes to the underlying file
    # should already be checked appropriately.
    files_including_symlinks = [os.path.abspath(f) for f in files]
    files = [x for x in files_including_symlinks if not os.path.islink(x)]

    CheckFn = Callable[
        [str, multiprocessing.pool.ThreadPool, Iterable[str]], CheckResults
    ]

    style_exempt_files = {
        # This file is mirrored from upstream llvm, so style checks need not
        # apply.
        os.path.join(toolchain_utils_root, "llvm_tools/revert_checker.py"),
    }

    style_checked_files = []
    for f in files:
        if f in style_exempt_files:
            print(f"NOTE: Skipping some checks on {f}; it's style-exempt.")
        else:
            style_checked_files.append(f)

    checks: list[tuple[str, CheckFn, list[str]]] = [
        ("check_cros_lint", check_cros_lint, style_checked_files),
        ("check_py_format", check_py_format, style_checked_files),
        (
            "check_py_types",
            functools.partial(check_py_types, mypy),
            style_checked_files,
        ),
        ("check_go_format", check_go_format, style_checked_files),
        ("check_json_format", check_json_format, style_checked_files),
        ("check_tests", check_tests, files),
        (
            "check_no_compiler_wrapper_changes",
            check_no_compiler_wrapper_changes,
            files_including_symlinks,
        ),
    ]

    # NOTE: As mentioned above, checks can block on threads they spawn in this
    # pool, so we need at least len(checks)+1 threads to avoid deadlock. Use *2
    # so all checks can make progress at a decent rate.
    num_threads = max(multiprocessing.cpu_count(), len(checks) * 2)
    start_time = datetime.datetime.now()

    # For our single print statement...
    spawn_print_lock = threading.RLock()

    def run_check(
        arg: tuple[str, CheckFn, Iterable[str]],
    ) -> tuple[str, CheckResults] | None:
        name, check_fn, files = arg
        with spawn_print_lock:
            if not files:
                print("*** Skipping %s; no applicable files")
                return None
            print("*** Spawning %s" % name)
        return name, check_fn(toolchain_utils_root, pool, files)

    with multiprocessing.pool.ThreadPool(num_threads) as pool:
        all_checks_ok = True
        all_autofix_commands = []
        for run_result in pool.imap_unordered(run_check, checks):
            if not run_result:
                continue
            check_name, result = run_result
            ok, autofix_commands = process_check_result(
                check_name, result, start_time
            )
            all_checks_ok = ok and all_checks_ok
            all_autofix_commands += autofix_commands

    # Run these after everything settles, so:
    # - we don't collide with checkers that are running concurrently
    # - we clearly print out everything that went wrong ahead of time, in case
    #   any of these fail
    if opts.autofix_allowed:
        try_autofix(
            all_autofix_commands, toolchain_utils_root, opts.force_autofix
        )

    if not all_checks_ok:
        return 1
    return 0
