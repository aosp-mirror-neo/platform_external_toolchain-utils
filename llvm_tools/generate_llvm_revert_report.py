# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Reports on all reverts applied and not applied to sys-devel/llvm.

Note that this is primarily intended to produce output that can be easily
pasted into a spreadsheet (read: the ChromeOS Mage's test matrix), so output is
in CSV format.
"""

import argparse
import csv
import dataclasses
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from typing import List, Set, TextIO

from cros_utils import cros_paths
from llvm_tools import get_llvm_hash
from llvm_tools import revert_checker


@dataclasses.dataclass(frozen=True)
class RevertInfo:
    """Information to write about a revert."""

    revert: revert_checker.Revert
    has_in_patches: bool
    subject: str


def list_upstream_cherrypicks(patches_json: Path) -> Set[str]:
    with patches_json.open(encoding="utf-8") as f:
        applicable_patches = [
            x
            for x in json.load(f)
            if not x.get("platforms") or "chromiumos" in x["platforms"]
        ]

    # Allow for arbitrary suffixes for patches; some have `-v2`, `_fixed`, etc.
    sha_re = re.compile(r"cherry/([a-fA-F0-9]{40})\b.*\.patch$")
    sha_like_patches = set()
    for p in applicable_patches:
        m = sha_re.match(p["rel_patch_path"])
        if m:
            sha_like_patches.add(m.group(1))

    return sha_like_patches


def fetch_commit_subject(llvm_git_dir: Path, sha: str) -> str:
    result = subprocess.run(
        ["git", "log", "--format=%s", "-n1", sha],
        check=True,
        cwd=llvm_git_dir,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        # Don't set stderr, since that should only be written to on error (and
        # `check=True`).
    )
    return result.stdout.strip()


def write_reverts_as_csv(write_to: TextIO, reverts: List[RevertInfo]):
    writer = csv.writer(write_to, quoting=csv.QUOTE_ALL)
    # Write the header.
    writer.writerow(("SHA", "Reverted SHA", "Has Revert", "Subject"))
    writer.writerows(
        (x.revert.sha, x.revert.reverted_sha, x.has_in_patches, x.subject)
        for x in reverts
    )


def main(argv: List[str]):
    # `cros_root` is hardcoded here, since:
    # - this one only reads tree state, and
    # - the person/automation invoking it is almost definitely invoking it _in
    #   the tree that it should run in_.
    cros_root = cros_paths.script_chromiumos_checkout_or_exit()

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
        "-C",
        "--git-dir",
        default=str(cros_root / cros_paths.LLVM_PROJECT),
        help="LLVM git directory to use.",
        # Note that this is left as `type=str` because that's what
        # `revert_checker` expects.
        type=str,
    )
    parser.add_argument(
        "--llvm-next", action="store_true", help="Use the llvm-next hash"
    )
    parser.add_argument(
        "--llvm-head",
        default="cros/upstream/main",
        help="ref to treat as 'origin/main' in the given LLVM dir.",
    )
    opts = parser.parse_args(argv)

    if opts.llvm_next:
        llvm_sha = get_llvm_hash.LLVMHash().GetCrOSLLVMNextHash()
    else:
        llvm_sha = get_llvm_hash.LLVMHash().GetCrOSCurrentLLVMHash(cros_root)

    logging.info("Resolved %r as the LLVM SHA to check.", llvm_sha)

    in_tree_cherrypicks = list_upstream_cherrypicks(
        cros_root / cros_paths.DEFAULT_PATCHES_PATH
    )
    logging.info("Identified %d local cherrypicks.", len(in_tree_cherrypicks))

    raw_reverts = revert_checker.find_reverts(
        opts.git_dir,
        llvm_sha,
        opts.llvm_head,
    )

    llvm_dir = Path(opts.git_dir)
    # Sort by `has_in_patches`, since that ordering is easier to visually scan.
    # Note that `sorted` is stable, so any ordering in `find_reverts` will be
    # preserved secondary to the `has_in_patches` ordering. Reverts not in
    # PATCHES.json will appear earlier than those that are.
    reverts = sorted(
        (
            RevertInfo(
                revert=revert,
                subject=fetch_commit_subject(llvm_dir, revert.sha),
                has_in_patches=revert.sha in in_tree_cherrypicks,
            )
            for revert in raw_reverts
        ),
        key=lambda x: x.has_in_patches,
    )

    print()
    print("CSV summary of reverts:")
    write_reverts_as_csv(sys.stdout, reverts)
