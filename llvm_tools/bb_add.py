# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs `bb add`, with additional convenience features."""

import argparse
import logging
import os
from pathlib import Path
import shlex
import sys
from typing import Iterable

from cros_utils import gerrit_utils
from llvm_tools import chroot
from llvm_tools import cros_cls
from llvm_tools import get_llvm_hash
from llvm_tools import llvm_next


class UntrustedCLsError(Exception):
    """Raised when untrusted CLs are detected and not allowed."""


DEFAULT_LLVM_NEXT_BUILDERS = ("chromeos/staging/staging-build-chromiumos-sdk",)


def generate_bb_add_command(
    extra_cls: Iterable[gerrit_utils.ChangeListURL],
    bots: Iterable[str],
    tags: Iterable[str],
) -> list[str]:
    """Generates a `bb add` command.

    Args:
        extra_cls: A list of extra CLs to add to the run.
        bots: Bots that should be spawned by this command, e.g.,
            `chromeos/staging/staging-build-chromiumos-sdk`.
        tags: Tags that should be applied to the bot invocation(s). This can
            make searching for the invocations easier using tools like `bb ls`.

    Returns:
        A command that would spawn the requested builders in the requested
        configuration.
    """
    cls: list[gerrit_utils.ChangeListURL] = []
    if extra_cls:
        cls += extra_cls

    cmd = ["bb", "add"]
    for cl in cls:
        cmd += ("-cl", cl.shorthand_url_without_http())

    for tag in tags:
        cmd += ("-t", tag)
    cmd += bots
    return cmd


def is_pointless_llvm_next_invocation(chromeos_tree: Path) -> bool:
    """Returns False if llvm-next testing is likely to be useful."""
    if not llvm_next.LLVM_NEXT_MANIFEST_CL:
        logging.info("Tests seem pointless: LLVM_NEXT_MANIFEST_CL is not set.")
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


def fetch_llvm_next_deps_or_exit(
    main_cl: gerrit_utils.ChangeListURL,
    chromeos_tree: Path,
    *,
    untrusted_reject: bool,
    untrusted_ignore: bool,
) -> list[gerrit_utils.ChangeListURL]:
    """Fetches dependencies for the main CL and handles untrusted CLs."""
    logging.info("Fetching dependencies for main CL: %s", main_cl)
    deps = cros_cls.fetch_gerrit_deps_of_most_recent_patchset(
        main_cl, chromeos_tree
    )
    owners = cros_cls.fetch_current_toolchain_owners()
    owners.extend(llvm_next.TRUSTED_UPLOADERS)

    trusted, untrusted = cros_cls.partition_changes_by_uploader_trust(
        deps,
        owners,
        # NOTE: Add `main_cl` here since that always has a patchset, and it's
        # _theoretically_ possible for it to be untrusted (say someone uploads
        # it, then leaves the team, so is removed from OWNERS).
        trusted_allowlist=(
            llvm_next.LLVM_NEXT_TESTING_URL_ALLOWLIST + (main_cl,)
        ),
    )

    included_changes = list(trusted)

    if untrusted:
        if untrusted_reject:
            logging.error("Untrusted CLs detected:")
            for c in untrusted:
                logging.error("- %s by %s", c.cl_url, c.uploader)
            raise UntrustedCLsError(
                "Aborting due to untrusted CLs "
                "(requested by --untrusted-reject)"
            )

        if untrusted_ignore:
            logging.info("Ignoring untrusted CLs:")
            for c in untrusted:
                logging.info("- %s by %s", c.cl_url, c.uploader)
        else:
            print("Untrusted CLs detected:")
            for c in untrusted:
                print(f"- {c.cl_url} by {c.uploader}")

            try:
                response = input(
                    "\n\nAllow run with these untrusted CLs? [y/N]: "
                )
            except EOFError:
                response = "n"

            if response.strip().lower() != "y":
                raise UntrustedCLsError("Aborted by user.")

            included_changes.extend(untrusted)

    # b/520356087: the order that CLs are specified here is _identical_ to the
    # order in which they're applied on the bot. `gerrit deps` prints its walk
    # order by default, which means that we will very often have children
    # ordered before parents, which leads to merge conflicts on bots.
    #
    # We use relation chains to resolve and sort them.
    with gerrit_utils.default_gerrit_thread_pool() as executor:
        sorted_changes = gerrit_utils.resolve_and_sort_cl_dependencies(
            included_changes,
            executor=executor,
        )
    return [change.cl_url for change in sorted_changes]


def parse_opts(argv: list[str]) -> argparse.Namespace:
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
        type=gerrit_utils.ChangeListURL.parse,
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
    untrusted_group = parser.add_mutually_exclusive_group()
    untrusted_group.add_argument(
        "--untrusted-ignore",
        action="store_true",
        help="Ignore untrusted CLs and do not include them in the run.",
    )
    untrusted_group.add_argument(
        "--untrusted-reject",
        action="store_true",
        help="Reject the run if there are untrusted CLs.",
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


def main(argv: list[str]) -> None:
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

    extra_cls = list(opts.cl) if opts.cl else []

    if opts.llvm_next:
        main_cl = llvm_next.LLVM_NEXT_MANIFEST_CL
        if not main_cl:
            logging.error("LLVM_NEXT_MANIFEST_CL is not set in llvm_next.py")
            sys.exit(1)

        try:
            extra_cls.extend(
                fetch_llvm_next_deps_or_exit(
                    main_cl,
                    opts.chromeos_tree,
                    untrusted_reject=opts.untrusted_reject,
                    untrusted_ignore=opts.untrusted_ignore,
                )
            )
        except UntrustedCLsError as e:
            sys.exit(str(e))

    cmd = generate_bb_add_command(
        extra_cls=extra_cls,
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
