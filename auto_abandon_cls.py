# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Abandons CLs from the current user that haven't been updated recently.

Note that this needs to be run from inside a ChromeOS tree. Otherwise, the
`gerrit` tool this depends on won't be found.
"""

import argparse
import enum
import logging
import subprocess
from typing import List


class GerritSearchType(enum.Enum):
    """Specifies the kind of gerrit search for `enumerate_old_cls`."""

    EXTERNAL_NO_LLVM = enum.auto()
    LLVM_ONLY = enum.auto()
    INTERNAL_ONLY = enum.auto()

    def is_internal(self):
        return self is self.INTERNAL_ONLY


def gerrit_cmd(internal: bool) -> List[str]:
    cmd = ["gerrit"]
    if internal:
        cmd.append("--internal")
    return cmd


def enumerate_old_cls(
    old_days: int, search_type: GerritSearchType
) -> List[int]:
    """Returns CL numbers that haven't been updated in `old_days` days."""
    search_string = f"owner:me status:open age:{old_days}d"
    llvm_repo = "project:external/github.com/llvm/llvm-project"
    if search_type is GerritSearchType.EXTERNAL_NO_LLVM:
        search_string += f" -{llvm_repo}"
    elif search_type is GerritSearchType.LLVM_ONLY:
        search_string += f" {llvm_repo}"
    else:
        assert (
            search_type is GerritSearchType.INTERNAL_ONLY
        ), f"Unhandled search type: {search_type}"

    is_internal = search_type.is_internal()
    stdout = subprocess.run(
        gerrit_cmd(is_internal) + ["--raw", "search", search_string],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout
    # Sort for prettier output; it's unclear if Gerrit always sorts, and it's
    # cheap.
    lines = stdout.splitlines()
    if is_internal:
        # These are printed as `chrome-internal:NNNN`, rather than `NNNN`.
        chrome_internal_prefix = "chrome-internal:"
        assert all(x.startswith(chrome_internal_prefix) for x in lines), lines
        lines = [x[len(chrome_internal_prefix) :] for x in lines]
    return sorted(int(x) for x in lines)


def abandon_cls(cls: List[int], internal: bool) -> None:
    subprocess.run(
        gerrit_cmd(internal) + ["abandon"] + [str(x) for x in cls],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def detect_and_abandon_cls(
    old_days: int,
    dry_run: bool,
    search_type: GerritSearchType,
) -> None:
    old_cls = enumerate_old_cls(old_days, search_type)
    if not old_cls:
        logging.info("No CLs less than %d days old found; quit", old_days)
        return

    is_internal = search_type.is_internal()
    cl_namespace = "i" if is_internal else "c"
    logging.info(
        "Abandoning CLs: %s", [f"crrev.com/{cl_namespace}/{x}" for x in old_cls]
    )
    if dry_run:
        logging.info("--dry-run specified; skip the actual abandon part")
        return

    abandon_cls(old_cls, is_internal)


def main(argv: List[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--old-days",
        default=14,
        type=int,
        help="""
        How many days a CL needs to go without modification to be considered
        'old'.
        """,
    )
    parser.add_argument(
        "--old-days-llvm",
        default=60,
        type=int,
        help="""
        How many days a CL needs to go without modification to be considered
        'old', specifically for CLs to ChromeOS' LLVM project.
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually run the abandon command.",
    )
    opts = parser.parse_args(argv)

    logging.info("Checking for external, non-LLVM CLs...")
    detect_and_abandon_cls(
        old_days=opts.old_days,
        dry_run=opts.dry_run,
        search_type=GerritSearchType.EXTERNAL_NO_LLVM,
    )
    logging.info("Checking for external LLVM CLs...")
    detect_and_abandon_cls(
        old_days=opts.old_days_llvm,
        dry_run=opts.dry_run,
        search_type=GerritSearchType.LLVM_ONLY,
    )
    logging.info("Checking for internal CLs...")
    detect_and_abandon_cls(
        old_days=opts.old_days,
        dry_run=opts.dry_run,
        search_type=GerritSearchType.INTERNAL_ONLY,
    )
