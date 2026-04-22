# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Automatically manages LLVM's PGO profiles.

Specifically, this script:
    - generates & uploads new PGO profiles for llvm-next, if necessary
    - ensures that the revisions for said llvm-next profiles are in the
      associated manifest file
    - cleans old llvm profiles from the ebuild

Run this outside of the chroot.
"""

import argparse
import collections
import logging
from pathlib import Path
import re
import shlex
import subprocess
import textwrap

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import chroot
from llvm_tools import get_llvm_hash
from llvm_tools import llvm_next
from pgo_tools import pgo_utils


PROFDATA_REV_PREFIX = "gs://chromeos-localmirror/distfiles/llvm-profdata-r"
PROFDATA_REV_SUFFIX = ".xz"

# Path to LLVM's 9999 ebuild from chromiumos-overlay
LLVM_EBUILD_SUBPATH = Path("sys-devel", "llvm", "llvm-9999.ebuild")


class GsProfileCache:
    """Caches which LLVM revisions we have profile information for (in gs://).

    To use this:
        1. Create an instance of this class using `GsProfileCache.fetch()`.
        2. Check if we have profile information for a revision:
           `cache.has_profile_for_rev(123)`.
        3. Inform the cache that we have information for a revision:
           `cache.insert_profile(123, suffix="foo")`.
    """

    def __init__(self, profiles: dict[int, list[str]]):
        """Constructs an object.

        Args:
            profiles: A dict of profile rev to the list of suffixes for these
                profiles. An empty suffix implies there is no suffix (incl `-`)
                in the profile name. These should be sorted from oldest ->
                newest. No list should be empty.
        """
        self.profile_revs = profiles

    def has_profile_for_rev(self, rev: int) -> bool:
        return rev in self.profile_revs

    def has_profile(self, rev: int, suffix: str) -> bool:
        return self.has_profile_for_rev(rev) and any(
            x == suffix for x in self.profile_revs[rev]
        )

    def newest_profile_name_for(self, rev: int) -> str:
        """Gets the newest profile name for the given rev.

        This name is intended for use in the LLVM ebuild.
        """
        if not self.has_profile_for_rev(rev):
            raise KeyError(f"No profiles for r{rev}")
        suffix = self.profile_revs[rev][-1]
        if suffix:
            suffix = f"-{suffix}"
        return f"{rev}{suffix}"

    def insert_profile(self, rev: int, suffix: str) -> None:
        """Inserts a profile with `suffix` at `rev`.

        Assumes that the profile is newer than all other profiles at `rev`.
        """
        self.profile_revs.setdefault(rev, []).append(suffix)

    def num_profiles(self) -> int:
        return sum(len(x) for x in self.profile_revs.values())

    @classmethod
    def fetch(cls) -> "GsProfileCache":
        stdout = subprocess.run(
            [
                "gsutil",
                "ls",
                f"{PROFDATA_REV_PREFIX}*{PROFDATA_REV_SUFFIX}",
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            encoding="utf-8",
        ).stdout.strip()

        prof_re = re.compile(
            re.escape(PROFDATA_REV_PREFIX)
            + r"(\d+)(?:-(.+))?"
            + re.escape(PROFDATA_REV_SUFFIX)
        )
        profiles = collections.defaultdict(list)
        for line in stdout.splitlines():
            m = prof_re.fullmatch(line)
            if not m:
                if not line.strip():
                    continue
                raise ValueError(f"Unparseable line from gs://: {line!r}")
            profile_rev, suffix = m.groups()
            profiles[int(profile_rev)].append(suffix if suffix else "")

        # Sort these alphabetically, so getting the 'most recent' profile (by
        # name) is trivial.
        for v in profiles.values():
            v.sort()

        return cls(profiles)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--chromiumos-tree",
        type=Path,
        help="""
        Path to the root of the ChromeOS tree to edit. Autodetected if not
        specified.
        """,
    )
    parser.add_argument(
        "--clean-llvm",
        action="store_true",
        help="""
        If a profile needs to be generated and there are uncommitted changes in
        the LLVM source directory, clean the changes.
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually upload CLs, or generate new benchmark profiles.",
    )
    parser.add_argument(
        "--force-llvm-next-pgo-generation",
        action="store_true",
        help="""
        Skip checks to see if LLVM profiles already exist. If a profile already
        exists in gs://, these will not be uploaded. This primarily exists so
        we can continuously test the PGO profile pipeline.
        """,
    )
    opts = parser.parse_args(argv)

    if not opts.chromiumos_tree:
        opts.chromiumos_tree = chroot.FindChromeOSRootAboveToolchainUtils()
    return opts


def maybe_upload_new_llvm_next_profile(
    *,
    chromiumos_tree: Path,
    profile_cache: GsProfileCache,
    dry_run: bool,
    toolchain_utils: Path,
    clean_llvm: bool,
    force_generation: bool,
) -> None:
    llvm_next_rev = llvm_next.LLVM_NEXT_REV
    # NOTE: `profile_suffix` is intentionally hardcoded to ''. Profiles with
    # non-empty suffixes are expected to be manually generated, and uploaded by
    # `create_chroot_and_generate_pgo_profile.py`. This script will correctly
    # detect and roll to these, if necessary.
    profile_suffix = ""
    upload_profile = True
    if profile_cache.has_profile(llvm_next_rev, profile_suffix):
        if not force_generation:
            logging.info(
                "llvm-next profile %d already exists in gs://; no need to "
                "make a new one",
                llvm_next_rev,
            )
            return
        logging.info(
            "llvm-next profile already exists in gs://; forcing generation "
            "without upload."
        )
        upload_profile = False

    create_script = (
        toolchain_utils
        / "py"
        / "bin"
        / "pgo_tools"
        / "create_chroot_and_generate_pgo_profile"
    )

    llvm_next_branch = get_llvm_hash.DetectLatestLLVMBranch(
        chromiumos_tree, rev=llvm_next_rev
    )
    if not llvm_next_branch:
        raise ValueError(f"No LLVM branches found in CrOS for r{llvm_next_rev}")

    logging.info(
        "Generating a PGO profile for LLVM r%d from branch %s",
        llvm_next_rev,
        llvm_next_branch,
    )
    cmd: pgo_utils.Command = [
        create_script,
        f"--chromiumos-tree={chromiumos_tree}",
        f"--branch={llvm_next_branch}",
        f"--profile-suffix={profile_suffix}",
    ]
    logging.info(
        "Generating %s a PGO profile for LLVM r%d",
        "and uploading" if upload_profile else "without uploading",
        llvm_next_rev,
    )
    if upload_profile:
        cmd.append("--upload")
    if clean_llvm:
        cmd.append("--clean-llvm")

    if dry_run:
        logging.info(
            "Skipping PGO profile generation for llvm r%d due to --dry-run. "
            "Would run: %s",
            llvm_next_rev,
            shlex.join(str(x) for x in cmd),
        )
        profile_cache.insert_profile(llvm_next_rev, profile_suffix)
        return

    llvm_project = chromiumos_tree / cros_paths.LLVM_PROJECT
    if not clean_llvm and git_utils.has_discardable_changes(llvm_project):
        raise ValueError(
            f"Uncommitted changes exist in {llvm_project}. Please get rid of "
            "them before running this script (e.g., with "
            "`git clean -fd && git reset --hard HEAD`)"
        )

    initial_head = git_utils.resolve_ref(git_dir=llvm_project, ref="HEAD")
    try:
        pgo_utils.run(cmd)
    finally:
        logging.info("Restoring llvm-project to its original state...")
        git_utils.discard_changes_and_checkout(
            git_dir=llvm_project, ref=initial_head
        )

    if upload_profile:
        profile_cache.insert_profile(llvm_next_rev, profile_suffix)


def overwrite_llvm_pgo_listing(
    chromiumos_overlay: Path, profile_names: list[str]
) -> bool:
    ebuild = chromiumos_overlay / LLVM_EBUILD_SUBPATH
    contents = ebuild.read_text(encoding="utf-8")
    new_pgo_listing = "\t" + "\n\t".join(profile_names)

    array_start = "\nLLVM_PGO_PROFILE_REVS=(\n"
    array_start_index = contents.index(array_start)
    array_end_index = contents.index("\n)", array_start_index)

    new_contents = (
        contents[: array_start_index + len(array_start)]
        + new_pgo_listing
        + contents[array_end_index:]
    )
    if new_contents == contents:
        return False
    ebuild.write_text(new_contents, encoding="utf-8")
    return True


def update_llvm_ebuild_manifest(
    chromeos_tree: Path, chromiumos_overlay: Path
) -> None:
    overlay_relpath = chromiumos_overlay.relative_to(chromeos_tree)
    overlay_chroot_path = Path("/mnt") / "host" / "source" / overlay_relpath
    llvm_9999 = overlay_chroot_path / LLVM_EBUILD_SUBPATH
    ebuild_manifest_cmd = shlex.join(["ebuild", str(llvm_9999), "manifest"])
    logging.info("Running `%s` in the chroot...", ebuild_manifest_cmd)
    subprocess.run(
        ["cros_sdk", "--", "bash", "-c", ebuild_manifest_cmd],
        check=True,
        cwd=chromeos_tree,
    )


def create_llvm_pgo_ebuild_update(
    chromeos_root: Path,
    chromiumos_overlay: Path,
    profile_cache: GsProfileCache,
    dry_run: bool,
) -> str | None:
    current_llvm_sha = get_llvm_hash.LLVMHash().GetCrOSCurrentLLVMHash(
        chromeos_root
    )
    current_llvm_rev = (
        get_llvm_hash.GetCachedUpToDateReadOnlyLLVMRepo().GetRevisionFromHash(
            current_llvm_sha
        )
    )
    logging.info("Current LLVM revision is %d", current_llvm_rev)
    want_revisions = [current_llvm_rev]

    llvm_next_rev = llvm_next.LLVM_NEXT_REV
    if current_llvm_rev != llvm_next_rev:
        logging.info("llvm-next rev is r%d", llvm_next_rev)
        if profile_cache.has_profile_for_rev(llvm_next_rev):
            want_revisions.append(llvm_next_rev)
        else:
            logging.info(
                "No PGO profile exists for r%d; skip adding to profile list",
                llvm_next_rev,
            )

    want_names = [
        profile_cache.newest_profile_name_for(x) for x in want_revisions
    ]
    logging.info(
        "Expected LLVM PGO profile version(s) in ebuild: %s", want_names
    )

    made_change = overwrite_llvm_pgo_listing(chromiumos_overlay, want_names)
    if not made_change:
        logging.info("No LLVM ebuild changes made")
        return None

    # Skip the manifest update in this case, since the profile cache we're
    # using might have had a entry inserted by the profile generation stage of
    # this script.
    if dry_run:
        logging.info("Skipping manifest update; --dry-run was passed")
    else:
        update_llvm_ebuild_manifest(chromeos_root, chromiumos_overlay)
    return git_utils.commit_all_changes(
        chromiumos_overlay,
        textwrap.dedent(
            """\
            llvm: update PGO profile listing

            This CL was generated by toolchain-utils'
            pgo_tools/auto_update_llvm_pgo_profile.py script.

            BUG=b:337284701
            TEST=CQ
            """
        ),
    )


def main(argv: list[str]) -> None:
    my_dir = Path(__file__).resolve().parent

    pgo_utils.exit_if_in_chroot()

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )
    opts = parse_args(argv)

    chromiumos_tree = opts.chromiumos_tree
    chromiumos_overlay = chromiumos_tree / cros_paths.CHROMIUMOS_OVERLAY
    dry_run = opts.dry_run

    logging.info("Populating gs:// profile cache...")
    profile_cache = GsProfileCache.fetch()
    logging.info(
        "Found %d LLVM PGO profiles in gs://.", profile_cache.num_profiles()
    )

    maybe_upload_new_llvm_next_profile(
        chromiumos_tree=chromiumos_tree,
        profile_cache=profile_cache,
        dry_run=dry_run,
        toolchain_utils=my_dir.parent,
        clean_llvm=opts.clean_llvm,
        force_generation=opts.force_llvm_next_pgo_generation,
    )

    # NOTE: `in_dir=chromiumos_tree` here is critical, since this function
    # needs to enter the chroot to run `ebuild manifest`. Hence, the worktree
    # must be trivially reachable from within the chroot.
    with git_utils.create_worktree(
        chromiumos_overlay, in_dir=chromiumos_tree
    ) as worktree:
        maybe_sha = create_llvm_pgo_ebuild_update(
            chromiumos_tree,
            worktree,
            profile_cache,
            dry_run,
        )

    if not maybe_sha:
        logging.info("No changes made to LLVM ebuild; quit.")
        return

    if dry_run:
        logging.info(
            "LLVM ebuild changes committed as %s. --dry-run specified; quit.",
            maybe_sha,
        )
        return

    cls = git_utils.upload_to_gerrit(
        chromiumos_overlay,
        remote=git_utils.CROS_EXTERNAL_REMOTE,
        branch=git_utils.CROS_MAIN_BRANCH,
        ref=maybe_sha,
    )
    for cl in cls:
        git_utils.set_autoreview_topic_and_labels(chromiumos_overlay, cl)
    logging.info("%d CL(s) uploaded.", len(cls))
