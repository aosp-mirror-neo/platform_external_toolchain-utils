#!/usr/bin/env python3
# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Simple LLVM Bisection Script for use with the llvm-9999 ebuild.

Example usage with `git bisect`:

    cd path/to/llvm-project
    git bisect good <GOOD_HASH>
    git bisect bad <BAD_HASH>
    git bisect run \
        path/to/llvm_tools/llvm_simple_bisect.py --reset-llvm \
        --test "emerge-atlas package" \
        --search-error "some error that I care about"
"""

import argparse
import dataclasses
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional, Text

import chroot


# Git Bisection exit codes
EXIT_GOOD = 0
EXIT_BAD = 1
EXIT_SKIP = 125
EXIT_ABORT = 255


class AbortingException(Exception):
    """A nonrecoverable error occurred which should not depend on the LLVM Hash.

    In this case we will abort bisection unless --never-abort is set.
    """


@dataclasses.dataclass(frozen=True)
class CommandResult:
    """Results a command"""

    return_code: int
    output: Text

    def success(self) -> bool:
        """Checks if command exited successfully."""
        return self.return_code == 0

    def search(self, error_string: Text) -> bool:
        """Checks if command has error_string in output."""
        return error_string in self.output

    def exit_assert(
        self,
        error_string: Text,
        llvm_hash: Text,
        log_dir: Optional[Path] = None,
    ):
        """Exit program with error code based on result."""
        if self.success():
            decision, decision_str = EXIT_GOOD, "GOOD"
        elif self.search(error_string):
            if error_string:
                logging.info("Found failure and output contained error_string")
            decision, decision_str = EXIT_BAD, "BAD"
        else:
            if error_string:
                logging.info(
                    "Found failure but error_string was not found in results."
                )
            decision, decision_str = EXIT_SKIP, "SKIP"

        logging.info("Completed bisection stage with: %s", decision_str)
        if log_dir:
            self.log_result(log_dir, llvm_hash, decision_str)
        sys.exit(decision)

    def log_result(self, log_dir: Path, llvm_hash: Text, decision: Text):
        """Log command's output to `{log_dir}/{llvm_hash}.{decision}`.

        Args:
            log_dir: Path to the directory to use for log files
            llvm_hash: LLVM Hash being tested
            decision: GOOD, BAD, or SKIP decision returned for `git bisect`
        """
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"{llvm_hash}.{decision}"
        log_file.touch()

        logging.info("Writing output logs to %s", log_file)

        log_file.write_text(self.output, encoding="utf-8")

        # Fix permissions since sometimes this script gets called with sudo
        log_dir.chmod(0o666)
        log_file.chmod(0o666)


class LLVMRepo:
    """LLVM Repository git and workon information."""

    REPO_PATH = Path("/mnt/host/source/src/third_party/llvm-project")

    def __init__(self):
        self.workon: Optional[bool] = None

    def get_current_hash(self) -> Text:
        try:
            output = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=self.REPO_PATH,
                encoding="utf-8",
            )
            output = output.strip()
        except subprocess.CalledProcessError as e:
            output = e.output
            logging.error("Could not get current llvm hash")
            raise AbortingException
        return output

    def set_workon(self, workon: bool):
        """Toggle llvm-9999 mode on or off."""
        if self.workon == workon:
            return
        subcommand = "start" if workon else "stop"
        try:
            subprocess.check_call(
                ["cros_workon", "--host", subcommand, "sys-devel/llvm"]
            )
        except subprocess.CalledProcessError:
            logging.exception("cros_workon could not be toggled for LLVM.")
            raise AbortingException
        self.workon = workon

    def reset(self):
        """Reset installed LLVM version."""
        logging.info("Reseting llvm to downloaded binary.")
        self.set_workon(False)
        files_to_rm = Path("/var/lib/portage/pkgs").glob("sys-*/*")
        try:
            subprocess.check_call(
                ["sudo", "rm", "-f"] + [str(f) for f in files_to_rm]
            )
            subprocess.check_call(["emerge", "-C", "llvm"])
            subprocess.check_call(["emerge", "-G", "llvm"])
        except subprocess.CalledProcessError:
            logging.exception("LLVM could not be reset.")
            raise AbortingException

    def build(self, use_flags: Text) -> CommandResult:
        """Build selected LLVM version."""
        logging.info(
            "Building llvm with candidate hash. Use flags will be %s", use_flags
        )
        self.set_workon(True)
        try:
            output = subprocess.check_output(
                ["sudo", "emerge", "llvm"],
                env={"USE": use_flags, **os.environ},
                encoding="utf-8",
                stderr=subprocess.STDOUT,
            )
            return_code = 0
        except subprocess.CalledProcessError as e:
            return_code = e.returncode
            output = e.output
        return CommandResult(return_code, output)


def run_test(command: Text) -> CommandResult:
    """Run test command and get a CommandResult."""
    logging.info("Running test command: %s", command)
    result = subprocess.run(
        command,
        check=False,
        encoding="utf-8",
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    logging.info("Test command returned with: %d", result.returncode)
    return CommandResult(result.returncode, result.stdout)


def get_use_flags(
    use_debug: bool, use_lto: bool, error_on_patch_failure: bool
) -> str:
    """Get the USE flags for building LLVM."""
    use_flags = []
    if use_debug:
        use_flags.append("debug")
    if not use_lto:
        use_flags.append("-thinlto")
        use_flags.append("-llvm_pgo_use")
    if not error_on_patch_failure:
        use_flags.append("continue-on-patch-failure")
    return " ".join(use_flags)


def abort(never_abort: bool):
    """Exit with EXIT_ABORT or else EXIT_SKIP if never_abort is set."""
    if never_abort:
        logging.info(
            "Would have aborted but --never-abort was set. "
            "Completed bisection stage with: SKIP"
        )
        sys.exit(EXIT_SKIP)
    else:
        logging.info("Completed bisection stage with: ABORT")
        sys.exit(EXIT_ABORT)


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simple LLVM Bisection Script for use with llvm-9999."
    )

    parser.add_argument(
        "--never-abort",
        action="store_true",
        help=(
            "Return SKIP (125) for unrecoverable hash-independent errors "
            "instead of ABORT (255)."
        ),
    )
    parser.add_argument(
        "--reset-llvm",
        action="store_true",
        help="Reset llvm with downloaded prebuilds before rebuilding",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Don't build or reset llvm, even if --reset-llvm is set.",
    )
    parser.add_argument(
        "--use-debug",
        action="store_true",
        help="Build llvm with assertions enabled",
    )
    parser.add_argument(
        "--use-lto",
        action="store_true",
        help="Build llvm with thinlto and PGO. This will increase build times.",
    )
    parser.add_argument(
        "--error-on-patch-failure",
        action="store_true",
        help="Don't add continue-on-patch-failure to LLVM use flags.",
    )

    test_group = parser.add_mutually_exclusive_group(required=True)
    test_group.add_argument(
        "--test-llvm-build",
        action="store_true",
        help="Bisect the llvm build instead of a test command/script.",
    )
    test_group.add_argument(
        "--test", help="Command to test (exp. 'emerge-atlas grpc')"
    )

    parser.add_argument(
        "--search-error",
        default="",
        help=(
            "Search for an error string from test if test has nonzero exit "
            "code. If test has a non-zero exit code but search string is not "
            "found, git bisect SKIP will be used."
        ),
    )
    parser.add_argument(
        "--log-dir",
        help=(
            "Save a log of each output to a directory. "
            "Logs will be written to `{log_dir}/{llvm_hash}.{decision}`"
        ),
    )

    return parser.parse_args()


def run(opts: argparse.Namespace):
    # Validate path to Log dir.
    log_dir = opts.log_dir
    if log_dir:
        log_dir = Path(log_dir)
        if log_dir.exists() and not log_dir.is_dir():
            logging.error("argument --log-dir: Given path is not a directory!")
            raise AbortingException()

    # Get LLVM repo
    llvm_repo = LLVMRepo()
    llvm_hash = llvm_repo.get_current_hash()
    logging.info("Testing LLVM Hash: %s", llvm_hash)

    # Build LLVM
    if not opts.skip_build:

        # Get llvm USE flags.
        use_flags = get_use_flags(
            opts.use_debug, opts.use_lto, opts.error_on_patch_failure
        )

        # Reset LLVM if needed.
        if opts.reset_llvm:
            llvm_repo.reset()

        # Build new LLVM-9999.
        build_result = llvm_repo.build(use_flags)

        # Check LLVM-9999 build.
        if opts.test_llvm_build:
            logging.info("Checking result of build....")
            build_result.exit_assert(opts.search_error, llvm_hash, opts.log_dir)
        elif build_result.success():
            logging.info("LLVM candidate built successfully.")
        else:
            logging.error("LLVM could not be built.")
            logging.info("Completed bisection stage with: SKIP.")
            sys.exit(EXIT_SKIP)

    # Run Test Command.
    test_result = run_test(opts.test)
    logging.info("Checking result of test command....")
    test_result.exit_assert(opts.search_error, llvm_hash, log_dir)


def main():
    logging.basicConfig(level=logging.INFO)
    chroot.VerifyInsideChroot()
    opts = get_args()
    try:
        run(opts)
    except AbortingException:
        abort(opts.never_abort)
    except Exception:
        logging.exception("Uncaught Exception in main")
        abort(opts.never_abort)


if __name__ == "__main__":
    main()
