#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Ensures llvm-next is checked out in src/third_party/llvm-project.

This just ensures that your current commit is a strict child of the llvm-next
commit, to give flexibility for e.g., bisection across our downstream commits,
and for landing your own commits locally.

If llvm-next isn't checked out and you have no unstaged changes, this will check
the current branch of it out for you.
"""

import argparse
import logging
import subprocess
import sys

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import get_llvm_hash


def main(argv: list[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    cros_root = cros_paths.script_chromiumos_checkout_or_exit()
    llvm_project_dir = cros_root / cros_paths.LLVM_PROJECT

    llvm_hash = get_llvm_hash.LLVMHash()
    llvm_next_hash = llvm_hash.GetCrOSLLVMNextHash()
    logging.debug("llvm-next hash: %s", llvm_next_hash)

    if git_utils.is_ancestor(
        llvm_project_dir,
        parent=llvm_next_hash,
        child="HEAD",
        strict=True,
    ):
        logging.info("Current HEAD is llvm-next.")
        return

    logging.info(
        "Current HEAD is NOT strictly a child of llvm-next hash; "
        "checking out..."
    )

    if git_utils.has_discardable_changes(llvm_project_dir):
        sys.exit(
            "There are uncommitted or untracked changes in "
            f"{llvm_project_dir}. Refusing to check anything out."
        )

    logging.info("Finding llvm-next branch...")
    llvm_repo = get_llvm_hash.GetReadOnlyLLVMRepo()
    revision = llvm_repo.GetRevisionFromHash(llvm_next_hash)
    best_branch = get_llvm_hash.DetectLatestLLVMBranch(cros_root, revision)
    if not best_branch:
        sys.exit(f"No matching branches found for r{revision}")

    logging.info("Selected branch: %s", best_branch)
    logging.info("Checking out branch %s...", best_branch)
    subprocess.run(
        ("git", "checkout", best_branch),
        cwd=llvm_project_dir,
        check=True,
        stdin=subprocess.DEVNULL,
    )
