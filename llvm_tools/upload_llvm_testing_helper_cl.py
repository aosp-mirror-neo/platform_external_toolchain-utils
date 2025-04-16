# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Uploads an LLVM 'testing helper' CL.

These CLs make the validation of LLVM easier, and do things like:
- allowing patches to be disabled if they no longer apply
- disabling warnings
"""

import argparse
import logging
from pathlib import Path
from typing import List

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import chroot


# Text to add to the bottom of ebuild hooks.
DISABLE_WARNINGS_BLOCK = r"""

# Disable -Werror where possible, so more serious issues (e.g., compiler
# crashes) can be more easily surfaced.
cros_pre_src_configure_disable_werror() {
  # Add the special env var to toolchain/fatal_clang_warnings. There's logic
  # in Chromite to search for & upload these directories on all builds,
  # including failing ones.
  local d="${CROS_ARTIFACTS_TMP_DIR}/toolchain/fatal_clang_warnings"
  export CFLAGS+=" -D_CROSTC_FORCE_DISABLE_WERROR=${d} "
  export CXXFLAGS+=" -D_CROSTC_FORCE_DISABLE_WERROR=${d} "
  # Set these for ec ebuilds, since those ignore CFLAGS/CXXFLAGS
  [[ -n "${_ECLASS_CROS_EC:-}" ]] && export EXTRA_CFLAGS+=" -D_CROSTC_FORCE_DISABLE_WERROR=${d} "

  # Also export an env var, since some build systems will ignore our CFLAGS
  # but not filter the environment.
  export FORCE_DISABLE_WERROR=1
}
"""

# Text to add to the bottom of `profiles/base/use.force`.
USE_FORCE_BLOCK = r"""

# Allow PGO profile mismatches to continue silently. Without this, LLVM builds
# will fail unless a PGO profile has been generated (which shouldn't be
# blocking until we're ready to land llvm-next).
allow-pgo-mismatch
"""

COMMIT_MESSAGE = """\
DO NOT COMMIT: llvm-testing helper CL

This CL was automatically generated to facilitate LLVM testing.
The script that generated this is located at
src/third_party/toolchain-utils/llvm_tools/upload_llvm_testing_helper_cl.py.

BUG=None
TEST=None
"""


def add_force_rebuild_marker(chromiumos_overlay: Path):
    """Adds a marker to force this change to appear as a toolchain change."""
    # `touch`ing anything in `sys-devel/llvm/files` causes an LLVM revbump, and
    # causes all packages to be rebuilt.
    force_rebuild_file = (
        chromiumos_overlay / "sys-devel" / "llvm" / "files" / "force_rebuild"
    )
    force_rebuild_file.touch()


def add_use_force_block(chromiumos_overlay: Path):
    use_force = chromiumos_overlay / "profiles" / "base" / "use.force"
    # If this doesn't exist, that _can_ be worked with, but it's a smoke signal
    # (since e.g., maybe the file no longer takes effect). Have someone
    # investigate.
    if not use_force.exists():
        raise ValueError(f"No file found at {use_force}; refusing to patch")
    with use_force.open("a", encoding="utf-8") as f:
        f.write(USE_FORCE_BLOCK)


def add_disable_warnings_block(chromiumos_overlay: Path):
    ebuild_hooks = chromiumos_overlay / "profiles" / "base" / "profile.bashrc"
    # If this doesn't exist, that _can_ be worked with, but it's a smoke signal
    # (since e.g., maybe the file no longer takes effect). Have someone
    # investigate.
    if not ebuild_hooks.exists():
        raise ValueError(f"No file found at {ebuild_hooks}; refusing to patch")
    with ebuild_hooks.open("a", encoding="utf-8") as f:
        f.write(DISABLE_WARNINGS_BLOCK)


def create_helper_cl_commit_in_worktree_of(
    chromiumos_overlay: Path, tot: bool
) -> str:
    """Creates a commit containing the helper CL diff. Returns the SHA.commit"""
    with git_utils.create_worktree(chromiumos_overlay) as worktree:
        if tot:
            git_utils.fetch_and_checkout(
                worktree,
                remote=git_utils.CROS_EXTERNAL_REMOTE,
                branch=git_utils.CROS_MAIN_BRANCH,
            )

        logging.info("Adding helper changes to CL in %s...", worktree)
        add_force_rebuild_marker(worktree)
        add_use_force_block(worktree)
        add_disable_warnings_block(worktree)
        return git_utils.commit_all_changes(worktree, COMMIT_MESSAGE)


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
        "--chromeos-tree",
        type=Path,
        help="""
        The ChromeOS tree to update in. The `llvm-project` directory of this
        may also be consulted. Will try to autodetect if none is specified.
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Commit changes, but don't actually upload them.",
    )
    parser.add_argument(
        "--tot",
        action="store_true",
        help="""
        If passed, modified repos will be `git fetch`ed and this script will
        work on their main branches, rather than working on the version you
        have locally.
        """,
    )
    opts = parser.parse_args(argv)

    chromeos_tree = opts.chromeos_tree
    if not chromeos_tree:
        chromeos_tree = chroot.FindChromeOSRootAboveToolchainUtils()

    chromiumos_overlay = chromeos_tree / cros_paths.CHROMIUMOS_OVERLAY
    helper_sha = create_helper_cl_commit_in_worktree_of(
        chromiumos_overlay, tot=opts.tot
    )
    if opts.dry_run:
        logging.info(
            "--dry-run specified; not uploading new commit (%s).",
            helper_sha,
        )
        return

    # This logs the CL information, so no need to print anything after this.
    git_utils.upload_to_gerrit(
        git_repo=chromiumos_overlay,
        remote=git_utils.CROS_EXTERNAL_REMOTE,
        branch=git_utils.CROS_MAIN_BRANCH,
        ref=helper_sha,
    )
