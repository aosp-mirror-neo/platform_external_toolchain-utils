# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tracks recent SDK builder invocations.

This script prints the status of the N most recent SDK builder invocations, and
fails if all of them fail. The intent is for Chrotomation to run this regularly,
so the mage can be alerted if these builders are consistently failing.
"""

import argparse
import json
import logging
import sys

from llvm_tools import cros_cls
from llvm_tools import llvm_next


def main(argv: list[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-n",
        "--number-to-check",
        type=int,
        default=5,
        help="Number of builds to check (default: %(default)s)",
    )
    opts = parser.parse_args(argv)

    if not llvm_next.LLVM_NEXT_MANIFEST_CL:
        logging.info("No llvm-next manifest CL is registered; exiting cleanly.")
        return

    ls_args: list[str] = []

    # `ls` will show running/started builds, which we want to ignore.
    #
    # We could alternatively e.g., request `opts.number_to_check*2` builds and
    # filter the running ones out ourselves, but the number of running builds is
    # _technically_ not bounded. Prefer `or`ing a handful of `-predicate`s
    # instead.
    for status in cros_cls.BuilderStatus:
        if status.is_running:
            continue
        predicate = json.dumps(
            {
                "tags": [
                    {"key": "toolchain", "value": "non-cq-llvm-next-testing"}
                ],
                "status": status.value,
            }
        )
        ls_args += ("-predicate", predicate)
    ls_args += ("-n", str(opts.number_to_check))

    logging.info("Running `bb ls` - this may take a few dozen seconds...")
    builds = cros_cls.fetch_bb_ls_info(ls_args=ls_args)
    if not builds:
        logging.warning("No builds found.")
        sys.exit(1)

    for build in builds:
        logging.info(
            "Status: %s, Time: %s, Link: %s",
            build.status,
            build.create_time,
            cros_cls.builder_url(build.build_id),
        )

    if all(b.status.is_failure for b in builds):
        logging.error(
            "All %d most recent runs failed; exiting with error",
            len(builds),
        )
        sys.exit(1)

    logging.info("Success: Not all recent builds failed.")
