#!/usr/bin/env python3
# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Abandons CLs from the current user that haven't been updated recently.

Note that this needs to be run from inside a ChromeOS tree. Otherwise, the
`gerrit` tool this depends on won't be found.
"""

import argparse
import logging
import subprocess
import sys
from typing import List


def gerrit_cmd(internal: bool) -> List[str]:
    cmd = ["gerrit"]
    if internal:
        cmd.append("--internal")
    return cmd


def enumerate_old_cls(old_days: int, internal: bool) -> List[int]:
    """Returns CL numbers that haven't been updated in `old_days` days."""
    stdout = subprocess.run(
        gerrit_cmd(internal)
        + ["--raw", "search", f"owner:me status:open age:{old_days}d"],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout
    # Sort for prettier output; it's unclear if Gerrit always sorts, and it's
    # cheap.
    lines = stdout.splitlines()
    if internal:
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
    old_days: int, dry_run: bool, internal: bool
) -> None:
    old_cls = enumerate_old_cls(old_days, internal)
    if not old_cls:
        logging.info("No CLs less than %d days old found; quit", old_days)
        return

    cl_namespace = "i" if internal else "c"
    logging.info(
        "Abandoning CLs: %s", [f"crrev.com/{cl_namespace}/{x}" for x in old_cls]
    )
    if dry_run:
        logging.info("--dry-run specified; skip the actual abandon part")
        return

    abandon_cls(old_cls, internal)


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
        "--dry-run",
        action="store_true",
        help="Don't actually run the abandon command.",
    )
    opts = parser.parse_args(argv)

    logging.info("Checking for external CLs...")
    detect_and_abandon_cls(
        old_days=opts.old_days,
        dry_run=opts.dry_run,
        internal=False,
    )
    logging.info("Checking for internal CLs...")
    detect_and_abandon_cls(
        old_days=opts.old_days,
        dry_run=opts.dry_run,
        internal=True,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
