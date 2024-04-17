#!/usr/bin/env python3
# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs `bb add`, with additional convenience features."""

import argparse
import logging
import os
import shlex
import sys
from typing import Iterable, List

import cros_cls
import llvm_next


def generate_bb_add_command(
    use_llvm_next: bool,
    disable_werror: bool,
    extra_cls: Iterable[cros_cls.ChangeListURL],
    bots: Iterable[str],
) -> List[str]:
    cls: List[cros_cls.ChangeListURL] = []
    if use_llvm_next:
        if not llvm_next.LLVM_NEXT_TESTING_CLS:
            raise ValueError(
                "llvm-next testing requested, but no llvm-next CLs exist."
            )
        cls += llvm_next.LLVM_NEXT_TESTING_CLS

    if disable_werror:
        cls.append(llvm_next.DISABLE_WERROR_CL)

    if extra_cls:
        cls += extra_cls

    cmd = ["bb", "add"]
    for cl in cls:
        cmd += ("-cl", cl.crrev_url_without_http())
    cmd += bots
    return cmd


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
        "--llvm-next",
        action="store_true",
        help="Add the current llvm-next patch set.",
    )
    parser.add_argument(
        "--disable-werror",
        action="store_true",
        help="Add the 'disable -Werror' patch sets",
    )
    parser.add_argument(
        "--cl",
        action="append",
        type=cros_cls.ChangeListURL.parse,
        help="""
        CL to add to the `bb add` run. May be specified multiple times. In the
        form crrev.com/c/123456.
        """,
    )
    parser.add_argument("bot", nargs="+", help="Bot(s) to run `bb add` with.")
    opts = parser.parse_args(argv)

    cmd = generate_bb_add_command(
        use_llvm_next=opts.llvm_next,
        disable_werror=opts.disable_werror,
        extra_cls=opts.cl,
        bots=opts.bot,
    )
    logging.info("Running `bb add` command: %s...", shlex.join(cmd))
    # execvp raises if it fails, so no need to check.
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main(sys.argv[1:])
