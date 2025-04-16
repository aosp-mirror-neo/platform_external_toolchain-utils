# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Verify a given LLVM Branch Creation CL has the right patch stack.

The workflow generally for using this script is:

  1. The CrOSTC Mage requests a new LLVM branch of the format
     chromeos/llvm-rNNNN-N
  2. The CrOSTC Mage updates the branch locally using
     'ready_llvm_branch -r NNNNN --branch-number N --upload'
  3. The reviewer runs this script on the TOP of the CL stack.
  4. If this script exits with 0, the reviewer can run the provided
     gerrit commands to approve and submit the CL stack.

This script exits with 0 if the branch has been verified, 1 if it failed
to verify.

Examples:

    $ verify_patch_consistency.py --cl 5637483

    $ verify_patch_consistency.py --chromiumos-root ~/chromiumos --cl 5637483
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from typing import Any, Dict, List, Tuple

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import git_llvm_rev
from llvm_tools import llvm_project_base_commit
from llvm_tools import patch_utils


def verify_no_cq_depends(
    worktree_dir: Path,
    head: str,
    baseline_hash: str,
) -> bool:
    """Checks for Cq-Depends in the given branch.

    Returns:
        False if Cq-Depends were found and an error was printed.
    """
    git_log = git_utils.log(
        worktree_dir, head=head, stop_at=baseline_hash, log_format="%B"
    )
    if not git_log or git_log.isspace():
        raise ValueError("`git log` was empty - broken head/baseline_hash?")

    cq_depends = re.findall(r"^Cq-Depend:.*$", git_log, re.MULTILINE)
    if not cq_depends:
        return True

    # Write out a separator so this section is visually distinct.
    print(
        textwrap.dedent(
            """\
            ==================================================================

            Cq-Depends detected!

            These are _not_ checked by this script, but the commands that this
            script tells you to execute _will_ mass-stamp them. Please verify
            manually that either:
            - these are all merged (so you can ignore them), or
            - you are OK with automatically stamping and CQ+2'ing all of them

            If this is the case, rerun this script with the
            '--ignore-cq-depend' flag to skip this error. Otherwise, please
            work with the Mage to remove the `Cq-Depend`s.
            """
        ).strip(),
        file=sys.stderr,
    )

    print("Listing of Cq-Depend found in git log:", file=sys.stderr)
    for d in cq_depends:
        print(f"- {d}", file=sys.stderr)
    return False


def verify_in_worktree(
    toolchain_utils_dir: Path,
    llvm_src_dir: Path,
    patches_json: Path,
    chromiumos_overlay: Path,
    svn_revision: int,
    cl_ref: str,
    ignore_cq_depend: bool = False,
) -> bool:
    """Check equality between the local patches and an upstream CL reference.

    Args:
        toolchain_utils_dir: Path to toolchain_utils.
        llvm_src_dir: Path to an llvm-project dir.
        patches_json: A PATCHES.json file to apply.
        chromiumos_overlay: Path to chromiumos-overlay
        svn_revision: The synthetic SVN-style revision number to
            determine which patches apply.
        cl_ref: Upstream Change List reference name.
        ignore_cq_depend: if True, this won't warn about Cq-Depend footers in
            the log for `cl_ref`.

    Returns:
        True if the local patches match, False otherwise
    """
    # We have to fetch to make sure we know that the matching_hash
    # exists.
    git_utils.fetch(
        llvm_src_dir,
        remote=git_utils.CROS_EXTERNAL_REMOTE,
        branch=git_utils.CROS_MAIN_BRANCH,
    )
    # The cros external remote ("cros") uses its main branch
    # for the actual upstream revision, not the local chromeos main branch.
    # This is the same as the upstream/main.
    matching_hash = git_llvm_rev.translate_rev_to_sha(
        git_llvm_rev.LLVMConfig(git_utils.CROS_EXTERNAL_REMOTE, llvm_src_dir),
        git_llvm_rev.Rev(git_llvm_rev.MAIN_BRANCH, svn_revision),
    )
    with git_utils.create_worktree(
        llvm_src_dir, commitish=matching_hash
    ) as worktree_dir:
        llvm_project_base_commit.make_base_commit(
            toolchain_utils_dir,
            worktree_dir,
            svn_revision,
            chromiumos_overlay,
        )
        try:
            patch_utils.apply_all_from_json(
                svn_version=svn_revision,
                llvm_src_dir=worktree_dir,
                patches_json_fp=patches_json,
                patch_cmd=patch_utils.git_am_chromiumos_quiet,
            )
        except RuntimeError:
            apply_msg = (
                "FAILED TO VERIFY. Local patches did not apply.",
                "Make sure your PATCHES.json file is up to date.",
            )
            print("\n".join(apply_msg), file=sys.stderr)
            raise
        # We have to fetch again inside the worktree for the CL itself.
        git_utils.fetch(
            worktree_dir,
            remote=git_utils.CROS_EXTERNAL_REMOTE,
            branch=cl_ref,
        )

        diff = ref_diff(worktree_dir, "HEAD", "FETCH_HEAD")
        if diff:
            local_head = git_utils.resolve_ref(worktree_dir, "HEAD")
            fetch_head = git_utils.resolve_ref(worktree_dir, "FETCH_HEAD")
            diff_msg = (
                f"FAILED TO VERIFY. Local patches and CL {cl_ref} differ!",
                f"Comparing local HEAD {local_head} with"
                f" FETCH_HEAD {fetch_head}",
                "",
                diff,
            )
            print("\n".join(diff_msg), file=sys.stderr)
            return False

        if not (
            ignore_cq_depend
            or verify_no_cq_depends(
                worktree_dir,
                head="FETCH_HEAD",
                baseline_hash=matching_hash,
            )
        ):
            return False

    return True


def ref_diff(cwd: Path, ref1: str, ref2: str) -> str:
    """Compute diff between two git refs."""
    cmd = [
        "git",
        "diff",
        ref1,
        ref2,
        "--",
    ]
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
    ).stdout


def _gerrit_inspect(cl: int, chromiumos_root: Path) -> List[Dict[str, Any]]:
    """Gerrit command wrapper for easy mocking."""
    cmd = ("gerrit", "--json", "inspect", str(cl))
    return json.loads(
        subprocess.run(
            cmd,
            cwd=chromiumos_root,
            check=True,
            stdout=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
        ).stdout
    )


def parse_branch(cl: int, chromiumos_root: Path) -> Tuple[int, str]:
    """Extract the LLVM synthetic revision and git ref from a CL branch."""
    json_obj = _gerrit_inspect(cl, chromiumos_root)
    branch_name = json_obj[0]["branch"]
    ref = json_obj[0]["currentPatchSet"]["ref"]
    branch_regex = re.compile(r"llvm-r(\d+)")
    if match := branch_regex.search(branch_name):
        return int(match.group(1)), ref
    raise RuntimeError(
        f"Could not parse SVN revision from CL {cl}'s branch: '{branch_name}'"
    )


def _verified_message(svn_revision: int, cl: str, cl_ref: str) -> str:
    """Format the 'verified' message body and return it."""
    gerrit_cmd_template = "gerrit %s $(gerrit --raw --no-pager deps '%s') %s"
    gerrit_approve_cmd = gerrit_cmd_template % ("label-cr", cl, 2)
    gerrit_verify_cmd = gerrit_cmd_template % ("label-v", cl, 1)
    gerrit_cq_cmd = gerrit_cmd_template % ("label-cq", cl, 2)
    return "-" * 80 + textwrap.dedent(
        f"""
        VERIFIED! Local patches for r{svn_revision} are identical to the
        tree state at remote {cl_ref}. You can approve
        these changes together with the 'gerrit' command:

          {gerrit_approve_cmd} && {gerrit_verify_cmd}

        Once approved, you can submit these changes with CQ+2:

          {gerrit_cq_cmd}
        """
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse passed in argv list."""

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        "--ignore-cq-depend",
        action="store_true",
        help="If passed, this script won't error out on Cq-Depend footers.",
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
        "--cl",
        required=True,
        type=int,
        help="""
        Top of patch stack CL for a given revision branch.
        Expected to be in the format of just the CL number.
        """,
    )
    args = parser.parse_args(argv)

    # Set default chromiumos_root
    if not args.chromiumos_root:
        if repo_root := cros_paths.script_chromiumos_checkout():
            args.chromiumos_root = repo_root
        else:
            raise argparse.ArgumentError(
                chromiumos_root_action,
                "Could not find chromiumos root automatically."
                " Pass --chromiumos-root manually.",
            )

    # Set default llvm_dir
    if not args.llvm_dir:
        llvm_dir = args.chromiumos_root / cros_paths.LLVM_PROJECT
        if not (llvm_dir / ".git").is_dir():
            raise argparse.ArgumentError(
                llvm_dir_action,
                "Could not find llvm dir automatically. Pass --llvm-dir"
                " manually.",
            )
        args.llvm_dir = llvm_dir

    # Set default patch_file
    if not args.patch_file:
        args.patch_file = args.chromiumos_root / cros_paths.DEFAULT_PATCHES_PATH

    return args


def main(argv: List[str]) -> int:
    """Entry point."""
    args = parse_args(argv)
    svn_revision, cl_ref = parse_branch(args.cl, args.chromiumos_root)
    if not verify_in_worktree(
        toolchain_utils_dir=args.chromiumos_root / cros_paths.TOOLCHAIN_UTILS,
        llvm_src_dir=args.llvm_dir,
        patches_json=args.patch_file,
        chromiumos_overlay=args.chromiumos_root / cros_paths.CHROMIUMOS_OVERLAY,
        svn_revision=svn_revision,
        cl_ref=cl_ref,
        ignore_cq_depend=args.ignore_cq_depend,
    ):
        return 1
    print(_verified_message(svn_revision, args.cl, cl_ref))
    return 0
