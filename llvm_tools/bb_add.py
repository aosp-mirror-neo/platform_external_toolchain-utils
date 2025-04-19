# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs `bb add`, with additional convenience features."""

import argparse
import logging
import os
from pathlib import Path
import shlex
from typing import Iterable, List

from llvm_tools import chroot
from llvm_tools import cros_cls
from llvm_tools import get_llvm_hash
from llvm_tools import llvm_next


DEFAULT_LLVM_NEXT_BUILDERS = ("chromeos/staging/staging-build-chromiumos-sdk",)


def generate_bb_add_command(
    use_llvm_next: bool,
    extra_cls: Iterable[cros_cls.ChangeListURL],
    bots: Iterable[str],
    tags: Iterable[str],
) -> List[str]:
    """Generates a `bb add` command.

    Args:
        use_llvm_next: if True, all current llvm-next CLs will be added to the
            run.
        extra_cls: A list of extra CLs to add to the run.
        bots: Bots that should be spawned by this command, e.g.,
            `chromeos/staging/staging-build-chromiumos-sdk`.
        tags: Tags that should be applied to the bot invocation(s). This can
            make searching for the invocations easier using tools like `bb ls`.

    Returns:
        A command that would spawn the requested builders in the requested
        configuration.
    """
    cls: List[cros_cls.ChangeListURL] = []
    if use_llvm_next:
        if not llvm_next.LLVM_NEXT_TESTING_CLS:
            raise ValueError(
                "llvm-next testing requested, but no llvm-next CLs exist."
            )
        cls += llvm_next.LLVM_NEXT_TESTING_CLS

    if extra_cls:
        cls += extra_cls

    cmd = ["bb", "add"]
    for cl in cls:
        cmd += ("-cl", cl.crrev_url_without_http())

    for tag in tags:
        cmd += ("-t", tag)
    cmd += bots
    return cmd


def is_pointless_llvm_next_invocation(chromeos_tree: Path) -> bool:
    """Returns False if llvm-next testing is likely to be useful."""
    if not llvm_next.LLVM_NEXT_TESTING_CLS:
        logging.info(
            "Tests seem pointless: no llvm-next testing CLs are registered."
        )
        return True

    current_hash = get_llvm_hash.LLVMHash().GetCrOSCurrentLLVMHash(
        chromeos_tree
    )
    if current_hash == llvm_next.LLVM_NEXT_HASH:
        logging.info(
            "Tests seem pointless: current LLVM hash (%s) is the same as "
            "llvm-next",
            current_hash,
        )
        return True
    logging.info(
        "Testing seems useful; llvm-next hash is %s", llvm_next.LLVM_NEXT_HASH
    )
    return False


def parse_opts(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--add-llvm-next-verification-builders",
        action="store_true",
        help="""
        Add the default series of builders used to help verify llvm-next. Does
        not imply --llvm-next.
        """,
    )
    parser.add_argument(
        "--chromeos-tree",
        type=Path,
        help="""
        ChromeOS tree to make modifications in. Will be inferred if none is
        passed.
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the `bb` command, rather than running it.",
    )
    parser.add_argument(
        "--llvm-next",
        action="store_true",
        help="Add the current llvm-next patch set.",
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
    parser.add_argument(
        "--skip-if-pointless",
        action="store_true",
        help="""
        If this is passed, the `bb add` will be skipped. It's an error to pass
        this flag without `--llvm-next`.
        """,
    )
    parser.add_argument(
        "--tag",
        action="append",
        help="""
        Tag to add to the `bb add` invocation. May be specified multiple times.
        Tags are arbitrary text.
        """,
    )
    parser.add_argument(
        "bot", nargs="*", default=[], help="Bot(s) to run `bb add` with."
    )
    opts = parser.parse_args(argv)

    if opts.skip_if_pointless and not opts.llvm_next:
        parser.error("--skip-if-pointless may only be used with --llvm-next.")

    if opts.add_llvm_next_verification_builders:
        opts.bot += DEFAULT_LLVM_NEXT_BUILDERS

    if not opts.bot:
        parser.error("At least one bot must be specified.")

    if not opts.chromeos_tree:
        opts.chromeos_tree = chroot.FindChromeOSRootAboveToolchainUtils()

    return opts


def main(argv: List[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    opts = parse_opts(argv)

    if opts.skip_if_pointless and is_pointless_llvm_next_invocation(
        opts.chromeos_tree
    ):
        logging.info(
            "--skip-if-pointless passed for pointless invocation; quit."
        )
        return

    cmd = generate_bb_add_command(
        use_llvm_next=opts.llvm_next,
        extra_cls=opts.cl,
        bots=opts.bot,
        tags=opts.tag or (),
    )

    if opts.dry_run:
        logging.info(
            "--dry-run specified; would run: `%s` otherwise", shlex.join(cmd)
        )
        return

    logging.info("Running `bb add` command: %s...", shlex.join(cmd))
    # execvp raises if it fails, so no need to check.
    os.execvp(cmd[0], cmd)
