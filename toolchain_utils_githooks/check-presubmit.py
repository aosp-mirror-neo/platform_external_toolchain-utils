#!/usr/bin/env python3
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
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    Union,
)


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
        ("autofix_commands", List[List[str]]),
    ),
)


Command = Sequence[Union[str, os.PathLike]]
CheckResults = Union[List[Tuple[str, CheckResult]], CheckResult]

# Environment variable that's set to a nonempty value on bots. Used for
# skipping some tasks on CI. Other presubmit checks detect whether a bot is
# running the check in a similar way.
SWARMING_TASK_ID_ENV = "SWARMING_TASK_ID"

# Environment variables to forward to the `cros_sdk` invocation, if we're
# re-execing in the chroot.
CHROOT_FORWARDED_ENV = (SWARMING_TASK_ID_ENV,)

# The files and directories on which we run the mypy typechecker. The paths are
# relative to the root of the toolchain-utils repository.
MYPY_CHECKED_PATHS = (
    "afdo_tools/update_kernel_afdo.py",
    "check_portable_toolchains.py",
    "cros_utils/bugs.py",
    "cros_utils/bugs_test.py",
    "cros_utils/tiny_render.py",
    "llvm_tools",
    "pgo_tools",
    "pgo_tools_rust/pgo_rust.py",
    "rust_tools",
    "toolchain_utils_githooks/check-presubmit.py",
)

# Path to the script that lints changes to ${toolchain_utils}/llvm_patches.
LINT_LLVM_PATCHES_SCRIPT = "llvm_tools/lint_llvm_patches.py"


def run_command_unchecked(
    command: Command,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str]:
    """Runs a command in the given dir, returning its exit code and stdio."""
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
    )
    return p.returncode, p.stdout


def has_executable_on_path(exe: str) -> bool:
    """Returns whether we have `exe` somewhere on our $PATH"""
    return shutil.which(exe) is not None


def remove_deleted_files(files: Iterable[str]) -> List[str]:
    return [f for f in files if os.path.exists(f)]


def is_file_executable(file_path: str) -> bool:
    return os.access(file_path, os.X_OK)


# As noted in our docs, some of our Python code depends on modules that sit in
# toolchain-utils/. Add that to PYTHONPATH to ensure that things like `cros
# lint` are kept happy.
def env_with_pythonpath(toolchain_utils_root: str) -> Dict[str, str]:
    env = dict(os.environ)
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] += ":" + toolchain_utils_root
    else:
        env["PYTHONPATH"] = toolchain_utils_root
    return env


@dataclasses.dataclass(frozen=True)
class MyPyInvocation:
    """An invocation of mypy."""

    command: List[str]
    # Entries to add to PYTHONPATH, formatted for direct use in the PYTHONPATH
    # env var.
    pythonpath_additions: str


def maybe_get_or_install_mypy() -> Optional[MyPyInvocation]:
    """Finds the mypy executable and returns a command to invoke it.

    If mypy cannot be found and we're inside the chroot, this
    function installs mypy and returns a command to invoke it.

    If mypy cannot be found and we're outside the chroot, this
    returns None.

    Returns:
        An optional tuple containing:
            - the command to invoke mypy, and
            - any environment variables to set when invoking mypy
    """
    if has_executable_on_path("mypy"):
        return MyPyInvocation(command=["mypy"], pythonpath_additions="")

    pip = get_pip()
    if not pip:
        assert not is_in_chroot()
        return None

    def get_from_pip() -> Optional[MyPyInvocation]:
        rc, output = run_command_unchecked(pip + ["show", "mypy"])
        if rc:
            return None

        m = re.search(r"^Location: (.*)", output, re.MULTILINE)
        if not m:
            return None

        pythonpath = m.group(1)
        return MyPyInvocation(
            command=[
                "python3",
                "-m",
                "mypy",
            ],
            pythonpath_additions=pythonpath,
        )

    from_pip = get_from_pip()
    if from_pip:
        return from_pip

    if is_in_chroot():
        subprocess.check_call(pip + ["install", "--user", "mypy"])
        return get_from_pip()
    return None


def get_pip() -> Optional[List[str]]:
    """Finds pip and returns a command to invoke it.

    If pip cannot be found, this function attempts to install
    pip and returns a command to invoke it.

    If pip cannot be found, this function returns None.
    """
    have_pip = can_import_py_module("pip")
    if not have_pip:
        print("Autoinstalling `pip`...")
        subprocess.check_call(["python", "-m", "ensurepip"])
        have_pip = can_import_py_module("pip")

    if have_pip:
        return ["python", "-m", "pip"]
    return None


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
    exit_code, stdout_and_stderr = run_command_unchecked(
        command, cwd=toolchain_utils_root
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
    exit_code, stdout_and_stderr = run_command_unchecked(
        command, cwd=toolchain_utils_root
    )
    if exit_code:
        return CheckResult(
            ok=False,
            output="Failed getting black version; "
            f"stdstreams: {stdout_and_stderr}",
            autofix_commands=[],
        )

    black_version = stdout_and_stderr.strip()
    black_invocation: List[str] = [str(black), "--line-length=80"]
    command = black_invocation + ["--check"] + list(python_files)
    exit_code, stdout_and_stderr = run_command_unchecked(
        command, cwd=toolchain_utils_root
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
    if mypy.pythonpath_additions:
        new_pythonpath = (
            f"{mypy.pythonpath_additions}:{fixed_env['PYTHONPATH']}"
        )
        fixed_env["PYTHONPATH"] = new_pythonpath

    # Show the version number, mainly for troubleshooting purposes.
    cmd = mypy.command + ["--version"]
    exit_code, output = run_command_unchecked(
        cmd, cwd=toolchain_utils_root, env=fixed_env
    )
    if exit_code:
        return CheckResult(
            ok=False,
            output=f"Failed getting mypy version; stdstreams: {output}",
            autofix_commands=[],
        )
    # Prefix output with the version information.
    prefix = f"Using {output.strip()}, "

    cmd = list(mypy.command)
    cmd += (
        # Suppress mypy errors in files that aren't specified on `argv`. Until
        # toolchain-utils is overwhelmingly mypy-clean, this has to be the
        # default.
        "--follow-imports=silent",
        # b/338058766: in toolchain-utils, mypy will infer that each file
        # passed in is a package of its own. This leads to errors if one
        # file transitively imports another. `--explicit-package-bases` causes
        # mypy to treat $CWD (and other env var values) as the only package
        # bases, and $CWD == toolchain_utils_root.
        "--explicit-package-bases",
    )
    cmd += files
    exit_code, output = run_command_unchecked(
        cmd, cwd=toolchain_utils_root, env=fixed_env
    )
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
    ]
    return [(name, get_check_result_or_catch(task)) for name, task in tasks]


def file_is_relative_to(file: Path, potential_parent: Path) -> bool:
    """file.is_relative_to(potential_parent), but for Python < 3.9."""
    try:
        file.relative_to(potential_parent)
        return True
    except ValueError:
        return False


def is_file_in_any_of(file: Path, files_and_dirs: List[Path]) -> bool:
    """Returns whether `files_and_dirs` encompasses `file`.

    `files_and_dirs` is considered to encompass `file` if `files_and_dirs`
    contains `file` directly, or if it contains a directory that is a parent of
    `file`.

    Args:
        file: a path to check
        files_and_dirs: a list of directories to check
    """
    # This could technically be made sublinear, but it's running at most a few
    # dozen times on a `files_and_dirs` that's currently < 10 elems.
    return any(
        file == x or file_is_relative_to(file, x) for x in files_and_dirs
    )


def check_py_types(
    mypy: Optional[MyPyInvocation],
    toolchain_utils_root: str,
    thread_pool: multiprocessing.pool.ThreadPool,
    files: Iterable[str],
) -> CheckResults:
    """Runs static type checking for files in MYPY_CHECKED_FILES."""
    if not mypy:
        return CheckResult(
            ok=False,
            output="mypy not found. Please either enter a chroot "
            "or install mypy",
            autofix_commands=[],
        )

    path_root = Path(toolchain_utils_root)
    check_locations = [path_root / x for x in MYPY_CHECKED_PATHS]
    to_check = [
        x
        for x in files
        if x.endswith(".py") and is_file_in_any_of(Path(x), check_locations)
    ]

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


def find_chromeos_root_directory() -> Optional[str]:
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
    def try_run_cros_lint(cros_binary: str) -> Optional[CheckResult]:
        exit_code, output = run_command_unchecked(
            [cros_binary, "lint", "--"] + fixed_files,
            toolchain_utils_root,
            env=fixed_env,
        )

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

    def check_result_from_command(command: List[str]) -> CheckResult:
        exit_code, output = run_command_unchecked(
            command, toolchain_utils_root, env=fixed_env
        )
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


def check_go_format(toolchain_utils_root, _thread_pool, files):
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
    exit_code, output = run_command_unchecked(command, cwd=toolchain_utils_root)

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
        toolchain_utils_root, "py", "bin", "run_tests_for.py"
    )
    cmd = [run_tests_for, "--"]
    cmd += files
    exit_code, stdout_and_stderr = run_command_unchecked(
        cmd, toolchain_utils_root
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
) -> Tuple[bool, List[List[str]]]:
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
    all_autofix_commands: List[List[str]], toolchain_utils_root: str
) -> None:
    """Tries to run all given autofix commands, if appropriate."""
    if not all_autofix_commands:
        return

    exit_code, output = run_command_unchecked(
        ["git", "status", "--porcelain"], cwd=toolchain_utils_root
    )
    if exit_code != 0:
        print("Autofix aborted: couldn't get toolchain-utils git status.")
        return

    if output.strip():
        # A clean repo makes checking/undoing autofix commands trivial. A dirty
        # one... less so. :)
        print("Git repo seems dirty; skipping autofix.")
        return

    anything_succeeded = False
    for command in all_autofix_commands:
        exit_code, output = run_command_unchecked(
            command, cwd=toolchain_utils_root
        )

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


def find_repo_root(base_dir: str) -> Optional[str]:
    current = base_dir
    while current != "/":
        if os.path.isdir(os.path.join(current, ".repo")):
            return current
        current = os.path.dirname(current)
    return None


def is_in_chroot() -> bool:
    return os.path.exists("/etc/cros_chroot_version")


def maybe_reexec_inside_chroot(
    autofix: bool, install_deps_only: bool, infer_files: bool, files: List[str]
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
        rebase_path(__file__),
    ]

    if not autofix:
        args.append("--no_autofix")
    if install_deps_only:
        args.append("--install_deps_only")
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


def infer_files_from_env_or_die(toolchain_utils_root: Path) -> List[str]:
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


def files_that_modify_patches_checks(
    toolchain_utils_root: str, files: List[str]
) -> List[str]:
    llvm_patches = os.path.join(toolchain_utils_root, "llvm_patches/")
    return [
        x
        for x in files
        if x.startswith(llvm_patches) or x.endswith(LINT_LLVM_PATCHES_SCRIPT)
    ]


def check_patches_subdir(
    toolchain_utils_root: str,
    _thread_pool: multiprocessing.pool.ThreadPool,
    _files: Iterable[str],
) -> CheckResult:
    check_script = (
        Path(toolchain_utils_root) / "py" / "bin" / LINT_LLVM_PATCHES_SCRIPT
    )
    return_code, stdstreams = run_command_unchecked(
        [check_script], cwd=toolchain_utils_root
    )
    return CheckResult(
        ok=return_code == 0,
        output=stdstreams,
        autofix_commands=[],
    )


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no_autofix",
        dest="autofix",
        action="store_false",
        help="Don't run any autofix commands.",
    )
    parser.add_argument(
        "--no_enter_chroot",
        dest="enter_chroot",
        action="store_false",
        help="Prevent auto-entering the chroot if we're not already in it.",
    )
    parser.add_argument(
        "--install_deps_only",
        action="store_true",
        help="""
        Only install dependencies that would be required if presubmits were
        being run, and quit. This skips all actual checking.
        """,
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

    install_deps_only = opts.install_deps_only
    infer_files = opts.infer_files
    files = opts.files

    toolchain_utils_root = detect_toolchain_utils_root()
    if opts.enter_chroot:
        maybe_reexec_inside_chroot(
            opts.autofix, install_deps_only, infer_files, files
        )

    if not install_deps_only:
        if bool(files) == infer_files:
            parser.error(
                "Either `--infer_files` or a list of files must be passed, "
                "not both."
            )
        if infer_files:
            files = infer_files_from_env_or_die(Path(toolchain_utils_root))
            print(f"Inferred files to check: {files}")

    mypy = maybe_get_or_install_mypy()
    # If you ask for --no_enter_chroot, you're on your own for installing these
    # things.
    if is_in_chroot():
        if install_deps_only:
            print(
                "Dependency installation complete & --install_deps_only "
                "passed. Quit."
            )
            return 0
    elif install_deps_only:
        parser.error(
            "--install_deps_only is meaningless if the chroot isn't entered"
        )

    files = [os.path.abspath(f) for f in files]

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

    checks: List[Tuple[str, CheckFn, List[str]]] = [
        ("check_cros_lint", check_cros_lint, style_checked_files),
        ("check_py_format", check_py_format, style_checked_files),
        (
            "check_py_types",
            functools.partial(check_py_types, mypy),
            style_checked_files,
        ),
        ("check_go_format", check_go_format, style_checked_files),
        ("check_tests", check_tests, files),
        (
            "check_no_compiler_wrapper_changes",
            check_no_compiler_wrapper_changes,
            files,
        ),
    ]

    if x := files_that_modify_patches_checks(toolchain_utils_root, files):
        checks.append(("check_patches_subdir", check_patches_subdir, x))

    # NOTE: As mentioned above, checks can block on threads they spawn in this
    # pool, so we need at least len(checks)+1 threads to avoid deadlock. Use *2
    # so all checks can make progress at a decent rate.
    num_threads = max(multiprocessing.cpu_count(), len(checks) * 2)
    start_time = datetime.datetime.now()

    # For our single print statement...
    spawn_print_lock = threading.RLock()

    def run_check(
        arg: Tuple[str, CheckFn, Iterable[str]],
    ) -> Optional[Tuple[str, CheckResults]]:
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
    if opts.autofix:
        try_autofix(all_autofix_commands, toolchain_utils_root)

    if not all_checks_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
