# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Create and ready an LLVM Branch for ChromiumOS.

Use this script to create and set up an LLVM branch that can
be tracked by the ChromiumOS manifest.

Examples:

    # Make chromeos/llvm-r530567-1 locally, but doesn't upload or set the
    # upstream. Convenient for testing.
    $ ready_llvm_branch.py -r 530567

    # Make chromeos/llvm-r530567-1 and uploads it for review.
    $ ready_llvm_branch.py -r 530567 --upload

    # Make chromeos/llvm-r530567-2 and uploads it.
    $ ready_llvm_branch.py -r 530567 --branch-number 2 --upload
"""

import argparse
import logging
from pathlib import Path
import shlex
import subprocess
from typing import Callable, List

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import git_llvm_rev
from llvm_tools import llvm_project_base_commit
from llvm_tools import patch_utils


def _switch_branch(
    llvm_src_dir: Path,
    svn_revision: int,
    branch_number: int = 1,
    force_checkout: bool = False,
) -> str:
    start_sha = git_llvm_rev.translate_rev_to_sha(
        git_llvm_rev.LLVMConfig(git_utils.CROS_EXTERNAL_REMOTE, llvm_src_dir),
        git_llvm_rev.Rev(git_utils.CROS_MAIN_BRANCH, svn_revision),
    )
    switch_arg = "-C" if force_checkout else "-c"
    if force_checkout:
        git_utils.discard_changes_and_checkout(llvm_src_dir, start_sha)
    branch_name = f"chromeos/llvm-r{svn_revision}-{branch_number}"
    cmd = (
        "git",
        "switch",
        switch_arg,
        branch_name,
        start_sha,
    )
    subprocess.run(cmd, cwd=llvm_src_dir, check=True, stdin=subprocess.DEVNULL)
    return branch_name


def _apply_patches_locally(
    patches_json: Path,
    llvm_src_dir: Path,
    svn_revision: int,
    continue_on_failure: bool,
    apply_only: bool,
) -> None:
    patch_cmd: Callable = patch_utils.git_am_chromiumos
    if apply_only:
        patch_cmd = patch_utils.git_apply
    patch_utils.apply_all_from_json(
        svn_version=svn_revision,
        llvm_src_dir=llvm_src_dir,
        patches_json_fp=patches_json,
        patch_cmd=patch_cmd,
        continue_on_failure=continue_on_failure,
    )


def _maybe_upload_for_review(
    llvm_src_dir: Path, branch_name: str, dry_run: bool
) -> None:
    kwargs = {
        "remote": git_utils.CROS_EXTERNAL_REMOTE,
        "branch": branch_name,
        "topic": f"{branch_name}-patches",
    }
    if dry_run:
        cmd = git_utils.generate_upload_to_gerrit_cmd(**kwargs)
        upload_command_git = shlex.join(cmd)
        logging.warning(
            "Did not upload branch. You can do so manually with:"
            "\n\n  pushd %s && %s && popd",
            shlex.quote(str(llvm_src_dir)),
            upload_command_git,
        )
        return
    logging.info("Uploading branch...")
    git_utils.upload_to_gerrit(llvm_src_dir, **kwargs)


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse passed in argv list."""

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    git_revision_group = parser.add_mutually_exclusive_group(required=True)
    git_revision_group.add_argument(
        "-r",
        "--svn-revision",
        type=int,
        help="SVN Revision for which to apply patches. e.g. '516547'.",
    )
    git_revision_group.add_argument(
        "--head",
        action="store_true",
        help="Apply patches to current HEAD, using the HEAD's revision.",
    )

    chromiumos_root_action = parser.add_argument(
        "--chromiumos-root",
        type=Path,
        help="""
        Path to ChromiumOS root to detect the PATCHES.json.
        If neither this flag nor --patch-file are specified, it is
        autodetected.
        """,
    )
    parser.add_argument(
        "--patch-file",
        type=Path,
        help="""
        Path to PATCHES.json. If not specified, it is autodetected
        from --chromiumos-root.
        """,
    )
    parser.add_argument(
        "--force-checkout",
        action="store_true",
        help="""
        Runs `git clean` and `git reset --hard` before running, discarding
        any local changes. If you passed `--svn-revision` and the local branch
        that would be created exists already, this will also delete that branch.
        """,
    )
    llvm_dir_action = parser.add_argument(
        "--llvm-dir",
        type=Path,
        help="""
        Path to a ChromiumOS llvm-project directory. If not
        specified, it is autodetected from --chromiumos-root.
        """,
    )
    parser.add_argument(
        "--branch-number",
        default=1,
        type=int,
        help="""
        An index to avoid branch name conflicts. If a branch is already
        made in the upstream cros remote with the same name, and you want to make
        another cros branch for the same revision number, then pass this flag
        with a number other than '1'. Defaults to %(default)s.
        """,
    )
    upload_group = parser.add_mutually_exclusive_group()
    upload_group.add_argument(
        "--upload",
        action="store_true",
        help="Upload the branch to the correct destination branch.",
    )
    upload_group.add_argument(
        "-a",
        "--apply",
        action="store_true",
        help="Apply only, don't commit. Cannot be passed with --upload.",
    )
    parser.add_argument(
        "-c",
        "--continue-on-patch-failure",
        action="store_true",
        help="Skip patches that fail to apply. Useful with --apply.",
    )
    args = parser.parse_args(argv)
    if not args.chromiumos_root and not args.patch_file:
        if repo_root := cros_paths.script_chromiumos_checkout():
            args.chromiumos_root = repo_root
        else:
            raise argparse.ArgumentError(
                chromiumos_root_action,
                "Could not find chromiumos root automatically."
                " Pass --chromiumos-root manually.",
            )
    if not args.llvm_dir:
        llvm_dir = args.chromiumos_root / cros_paths.LLVM_PROJECT
        if not (llvm_dir / ".git").is_dir():
            raise argparse.ArgumentError(
                llvm_dir_action,
                "Could not find llvm dir automatically. Pass --llvm-dir"
                " manually.",
            )
        args.llvm_dir = llvm_dir

    if not args.patch_file:
        args.patch_file = args.chromiumos_root / cros_paths.DEFAULT_PATCHES_PATH

    return args


def main(sys_argv: List[str]) -> None:
    """Entry point."""
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )
    args = parse_args(sys_argv)
    branch_name = ""

    if args.svn_revision:
        svn_revision = args.svn_revision
        branch_name = _switch_branch(
            args.llvm_dir,
            svn_revision,
            args.branch_number,
            args.force_checkout,
        )
        logging.info("Created and switched to branch %s.", branch_name)
    elif args.head:
        if args.force_checkout:
            git_utils.discard_changes_and_checkout(args.llvm_dir, "HEAD")

        to_translate = git_utils.merge_base(
            args.llvm_dir,
            ["cros/upstream/main", "HEAD"],
        )
        if not to_translate:
            to_translate = "HEAD"
        svn_revision = git_llvm_rev.translate_sha_to_rev(
            git_llvm_rev.LLVMConfig(
                git_utils.CROS_EXTERNAL_REMOTE, args.llvm_dir
            ),
            to_translate,
        ).number
        logging.info("Applying to HEAD, as r%s", svn_revision)
    else:
        raise ValueError("Unreachable: either --head or -r is required")
    toolchain_utils_dir = args.chromiumos_root / cros_paths.TOOLCHAIN_UTILS
    chromiumos_overlay = args.chromiumos_root / cros_paths.CHROMIUMOS_OVERLAY
    if args.apply:
        llvm_project_base_commit.write_base_changes(
            toolchain_utils_dir,
            args.llvm_dir,
            svn_revision,
            chromiumos_overlay,
        )
        logging.info(
            "--apply passed, wrote cros base changes, but didn't commit."
        )
    else:
        llvm_project_base_commit.make_base_commit(
            toolchain_utils_dir,
            args.llvm_dir,
            svn_revision,
            chromiumos_overlay,
        )
        logging.info("Committed base commit.")
    _apply_patches_locally(
        args.chromiumos_root / cros_paths.DEFAULT_PATCHES_PATH,
        args.llvm_dir,
        svn_revision,
        args.continue_on_patch_failure,
        args.apply,
    )
    if not args.head:
        _maybe_upload_for_review(
            llvm_src_dir=args.llvm_dir,
            branch_name=branch_name,
            dry_run=not args.upload,
        )
    else:
        logging.warning(
            "--head passed: Cannot upload this branch for review, as"
            " changes may not be on a branch."
        )
