# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Uploads CLs necessary to run LLVM testing at an arbitrary SHA.

Also has the ability to:
- kick off a CQ run
- keep track of the last SHA that testing was requested on, and skip
  re-uploading if the SHA has not changed.
"""

import argparse
import dataclasses
import json
import logging
from pathlib import Path
import subprocess
import textwrap
from typing import List, Optional

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import atomic_write_file
from llvm_tools import chroot
from llvm_tools import get_llvm_hash
from llvm_tools import llvm_next
from llvm_tools import manifest_utils
from llvm_tools import upload_llvm_testing_helper_cl


def resolve_llvm_sha(sha_or_special: str) -> str:
    """Resolves the `--sha` flag to an LLVM SHA."""
    if sha_or_special == "llvm-next":
        return llvm_next.LLVM_NEXT_HASH
    if sha_or_special == "google3":
        return get_llvm_hash.LLVMHash().GetGoogle3LLVMHash()
    if sha_or_special == "google3-unstable":
        return get_llvm_hash.LLVMHash().GetGoogle3LLVMHash()
    # If this looks like a full git SHA, there's no need to sync the upstream
    # repo.
    if git_utils.is_full_git_sha(sha_or_special):
        return sha_or_special
    return git_utils.resolve_ref(
        get_llvm_hash.GetCachedUpToDateReadOnlyLLVMRepo().path, sha_or_special
    )


def read_last_tried_sha(retry_state: Path) -> Optional[str]:
    """Reads the last tried SHA from the state file."""
    try:
        with retry_state.open(encoding="utf-8") as f:
            return json.load(f)["last_tried_sha"]
    except FileNotFoundError:
        return None


def write_last_tried_sha(retry_state: Path, sha: str):
    """Writes the last tried SHA to the state file."""
    with atomic_write_file.atomic_write(retry_state) as f:
        json.dump({"last_tried_sha": sha}, f)


@dataclasses.dataclass(frozen=True)
class UploadedCLs:
    """Listing of CL numbers uploaded by a function."""

    internal: List[int]
    external: List[int]


def upload_one_cl_to_main(
    git_dir: Path, sha: str, remote: str, topic: Optional[str] = None
) -> int:
    """Uploads exactly one SHA from `git_dir`. Returns the CL number.

    Raises:
        AssertionError if more than one CL was uploaded.
    """
    cl_ids = git_utils.upload_to_gerrit(
        git_dir,
        remote=remote,
        branch=git_utils.CROS_MAIN_BRANCH,
        ref=sha,
        topic=topic,
    )
    assert len(cl_ids) == 1, f"Expected to upload one CL; uploaded {cl_ids}"
    return cl_ids[0]


def create_and_upload_test_helpers_cl(
    chromeos_tree: Path,
    dry_run: bool,
    tot: bool,
) -> int:
    """Creates & uploads the LLVM 'test helper' CL.

    Returns:
        The CL number of the test-helper CL, an int referencing an external CL.
        If dry_run is passed, returns 0.
    """
    chromiumos_overlay = chromeos_tree / cros_paths.CHROMIUMOS_OVERLAY
    sha = upload_llvm_testing_helper_cl.create_helper_cl_commit_in_worktree_of(
        chromiumos_overlay, tot
    )
    if dry_run:
        logging.info(
            "--dry-run passed; skipping upload of test-helpers CL %s", sha
        )
        return 0
    return upload_one_cl_to_main(
        chromiumos_overlay, sha, remote=git_utils.CROS_EXTERNAL_REMOTE
    )


def build_manifest_commit_message(
    llvm_sha: str,
    llvm_rev: int,
    cq_depend_external: Optional[int],
) -> str:
    msg = textwrap.dedent(
        f"""\
        toolchain.xml: update llvm to {llvm_sha} (r{llvm_rev})

        BUG=None
        TEST=CQ
        """
    )
    if cq_depend_external:
        msg += f"\n\nCq-Depend: chromium:{cq_depend_external}"
    return msg


def create_and_upload_manifest_cl(
    *,
    chromeos_tree: Path,
    llvm_sha: str,
    llvm_rev: int,
    cq_depend_external: Optional[int],
    dry_run: bool,
    topic: Optional[str],
    tot: bool,
) -> int:
    """Creates & uploads the LLVM update manifest CL.

    Returns:
        The CL number of the manifest CL, an int referencing an internal CL. If
        dry_run is passed, returns `0`.
    """
    manifest_internal = chromeos_tree / "manifest-internal"
    remote = git_utils.CROS_INTERNAL_REMOTE
    with git_utils.create_worktree(manifest_internal) as worktree:
        if tot:
            git_utils.fetch_and_checkout(
                worktree,
                remote=remote,
                branch=git_utils.CROS_MAIN_BRANCH,
            )

        manifest_utils.update_chromeos_manifest_in_manifest_dir(
            llvm_sha,
            worktree,
            chromeos_tree=chromeos_tree,
        )
        commit_msg = build_manifest_commit_message(
            llvm_sha, llvm_rev, cq_depend_external
        )
        sha = git_utils.commit_all_changes(worktree, commit_msg)

    if dry_run:
        logging.info("--dry-run passed; skipping upload of manifest CL %s", sha)
        return 0

    return upload_one_cl_to_main(
        manifest_internal,
        sha,
        remote=remote,
        topic=topic,
    )


def add_cl_comment(
    chromeos_tree: Path,
    cl_id: int,
    internal: bool,
    comment: str,
):
    """Creates & uploads the LLVM update manifest CL.

    Returns:
        The CL number of the manifest CL, an int referencing an internal CL.
    """
    cmd = ["gerrit"]
    if internal:
        cmd.append("--internal")
    cmd += ("message", str(cl_id), comment)
    subprocess.run(
        cmd,
        check=True,
        cwd=chromeos_tree,
        stdin=subprocess.DEVNULL,
    )


def create_and_upload_cls(
    *,
    chromeos_tree: Path,
    llvm_sha: str,
    llvm_rev: int,
    include_test_helpers: bool,
    dry_run: bool,
    manifest_gerrit_topic: Optional[str],
    tot: bool,
) -> UploadedCLs:
    external_cls = []
    if include_test_helpers:
        logging.info("Uploading test-helper CL...")
        test_helper_cl = create_and_upload_test_helpers_cl(
            chromeos_tree, dry_run, tot
        )
        external_cls.append(test_helper_cl)
    else:
        test_helper_cl = None
    logging.info("Creating LLVM update CL...")
    manifest_cl = create_and_upload_manifest_cl(
        chromeos_tree=chromeos_tree,
        llvm_sha=llvm_sha,
        llvm_rev=llvm_rev,
        cq_depend_external=test_helper_cl,
        dry_run=dry_run,
        topic=manifest_gerrit_topic,
        tot=tot,
    )
    # Notably, this is meant to catch `test_helper_cl == 0` (dry_run) or
    # `test_helper_cl == None` (if none was uploaded)
    if test_helper_cl:
        add_cl_comment(
            chromeos_tree,
            test_helper_cl,
            internal=False,
            comment=f"Corresponding Manifest update: crrev.com/i/{manifest_cl}",
        )
    return UploadedCLs(
        internal=[manifest_cl],
        external=external_cls,
    )


def make_gerrit_cq_dry_run_command(cls: List[int], internal: bool) -> List[str]:
    assert cls, "Can't make a dry-run command with no CLs to dry-run."
    cmd = ["gerrit"]
    if internal:
        cmd.append("--internal")
    cmd.append("label-cq")
    cmd += (str(x) for x in cls)
    cmd.append("1")
    return cmd


def cq_dry_run_cls(chromeos_tree: Path, cls: UploadedCLs):
    """Sets CQ+1 on the given uploaded CL listing."""
    # At the time of writing, this is expected given the context of the script.
    # Can easily refactor to make `cls.internal` optional, though.
    gerrit_cmds = []
    assert cls.internal, "LLVM update without internal CLs?"
    gerrit_cmds.append(
        make_gerrit_cq_dry_run_command(cls.internal, internal=True)
    )
    if cls.external:
        gerrit_cmds.append(
            make_gerrit_cq_dry_run_command(cls.external, internal=False)
        )
    for cmd in gerrit_cmds:
        subprocess.run(
            cmd,
            check=True,
            cwd=chromeos_tree,
            stdin=subprocess.DEVNULL,
        )


def parse_opts(argv: List[str]) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--chromeos-tree",
        type=Path,
        help="""
        ChromeOS tree to make modifications in. Will be inferred if none
        is passed.
        """,
    )
    parser.add_argument(
        "--cq",
        action="store_true",
        help="After uploading, set CQ+1 on the CL(s) that were uploaded.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="If passed, only commit changes locally; don't upload them.",
    )
    parser.add_argument(
        "--include-llvm-test-helper-cls",
        action="store_true",
        help="""
        Also upload CL(s) meant to ease LLVM testing. Namely, this will include
        logic to disable `-Werror` on packages, and logic to disable patches
        that no longer apply to LLVM.
        """,
    )
    parser.add_argument(
        "--manifest-gerrit-topic",
        help="""
        If provided, the internal-manifest CL will be uploaded with the given
        Gerrit topic. This is helpful to associate many CLs over time.
        """,
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
    parser.add_argument(
        "--retry-state",
        type=Path,
        help="""
        If passed, this will keep script state in the given file. At the
        moment, this file is only used to ensure that subsequent runs of this
        script don't trigger identical uploads.
        """,
    )
    parser.add_argument(
        "--sha",
        required=True,
        help="""
        SHA to use. This can either be an LLVM SHA, or a special value:
        `llvm-next`, `google3` or `google3-unstable`.
        """,
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    opts = parse_opts(argv)
    dry_run = opts.dry_run
    chromeos_tree = opts.chromeos_tree
    if not chromeos_tree:
        chromeos_tree = chroot.FindChromeOSRootAboveToolchainUtils()

    new_sha = resolve_llvm_sha(opts.sha)
    logging.info("Using LLVM SHA %s...", new_sha)
    if opts.retry_state:
        last_tried_sha = read_last_tried_sha(opts.retry_state)
        if last_tried_sha == new_sha:
            logging.info("New SHA is the same as the last tried SHA; quit.")
            return
        logging.info(
            "New SHA is different than the last tried SHA (%s).", last_tried_sha
        )

    logging.info("Getting LLVM revision for SHA %s...", new_sha)
    new_rev = (
        get_llvm_hash.GetCachedUpToDateReadOnlyLLVMRepo().GetRevisionFromHash(
            new_sha
        )
    )
    logging.info("LLVM SHA %s == r%d", new_sha, new_rev)
    uploaded_cls = create_and_upload_cls(
        chromeos_tree=chromeos_tree,
        llvm_sha=new_sha,
        llvm_rev=new_rev,
        include_test_helpers=opts.include_llvm_test_helper_cls,
        dry_run=dry_run,
        manifest_gerrit_topic=opts.manifest_gerrit_topic,
        tot=opts.tot,
    )

    if dry_run:
        logging.info("--dry-run passed; exiting")
        return

    if opts.cq:
        logging.info("Setting CQ+1 on the CLs...")
        cq_dry_run_cls(chromeos_tree, uploaded_cls)

    if opts.retry_state:
        write_last_tried_sha(opts.retry_state, new_sha)
