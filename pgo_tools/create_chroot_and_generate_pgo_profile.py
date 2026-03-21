# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""This script generates a PGO profile for llvm-next.

Do not run it inside of a chroot. It establishes a chroot of its own.
"""

import argparse
import dataclasses
import logging
from pathlib import Path
import re
import shlex
import shutil

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import chroot
from llvm_tools import get_llvm_hash
from pgo_tools import pgo_utils


SDK_VERSION_CONF_SUBDIR = (
    cros_paths.CHROMIUMOS_OVERLAY
    / "chromeos"
    / "binhost"
    / "host"
    / "sdk_version.conf"
)


@dataclasses.dataclass(frozen=True)
class ChrootInfo:
    """Describes a unique chroot, and the SDK version to pin it to."""

    chroot_name: str
    out_dir_name: str
    sdk_version: str


def detect_bootstrap_sdk_version(repo_root: Path) -> str:
    sdk_version_conf = repo_root / SDK_VERSION_CONF_SUBDIR
    bootstrap_version_re = re.compile(
        r'^BOOTSTRAP_FROZEN_VERSION="([^"]+)"$',
        re.MULTILINE,
    )
    results = bootstrap_version_re.findall(
        sdk_version_conf.read_text(encoding="utf-8")
    )
    if len(results) != 1:
        raise ValueError(
            f"Expected exactly one match in {sdk_version_conf} for "
            f"{bootstrap_version_re}; found {len(results)}"
        )
    return results[0]


def create_fresh_chroot(
    repo_root: Path,
    chroot_info: ChrootInfo,
) -> None:
    """Creates a chroot. If it already exists, replaces it."""
    pgo_utils.run(
        [
            "cros_sdk",
            "--replace",
            f"--chroot={chroot_info.chroot_name}",
            f"--out-dir={chroot_info.out_dir_name}",
            f"--sdk-version={chroot_info.sdk_version}",
            "--",
            "true",
        ],
        cwd=repo_root,
    )


def generate_pgo_profile(
    *,
    repo_root: Path,
    chroot_info: ChrootInfo,
    chroot_output_file: Path,
    sha: str,
    clean_llvm: bool,
) -> None:
    """Generates a PGO profile to `chroot_output_file`."""
    cros_sdk: pgo_utils.Command = [
        "cros_sdk",
        f"--chroot={chroot_info.chroot_name}",
        f"--out-dir={chroot_info.out_dir_name}",
        f"--sdk-version={chroot_info.sdk_version}",
        "--",
    ]
    llvm_project = repo_root / cros_paths.LLVM_PROJECT
    if clean_llvm:
        git_utils.discard_changes_and_checkout(git_dir=llvm_project, ref=sha)
    else:
        git_utils.checkout(git_dir=llvm_project, ref=sha)

    logging.info("Working on all toolchain projects")
    workon_llvm = (
        cros_paths.CHROOT_SOURCE_ROOT
        / cros_paths.TOOLCHAIN_UTILS
        / "llvm_tools"
        / "workon_llvm.sh"
    )
    pgo_utils.run(
        cros_sdk
        + [
            workon_llvm,
            # No need to set any boards up here.
            "-",
        ],
        cwd=repo_root,
    )
    logging.info("Running PGO profile generation script inside of the chroot")
    generate_pgo_profile_path = (
        cros_paths.CHROOT_SOURCE_ROOT
        / cros_paths.TOOLCHAIN_UTILS_PYBIN
        / "pgo_tools"
        / "generate_pgo_profile.py"
    )
    pgo_utils.run(
        cros_sdk
        + [
            generate_pgo_profile_path,
            f"--output={chroot_output_file}",
        ],
        cwd=repo_root,
    )


def compress_pgo_profile(pgo_profile: Path) -> Path:
    """Compresses a PGO profile for upload to gs://."""
    pgo_utils.run(
        ["xz", "-9", "-k", pgo_profile],
    )
    return Path(str(pgo_profile) + ".xz")


def translate_chroot_path_to_out_of_chroot(
    repo_root: Path, path: Path, info: ChrootInfo
) -> Path:
    """Translates a chroot path into an out-of-chroot path."""
    path_str = str(path)
    assert path_str.startswith("/tmp"), path
    # Remove the leading `/` from the output file so it joins properly.
    return repo_root / info.out_dir_name / str(path)[1:]


def determine_upload_command(
    profile_path: Path, rev: int, suffix: str
) -> pgo_utils.Command:
    """Returns a command that can be used to upload our PGO profile."""
    profile_name = f"llvm-profdata-r{rev}"
    if suffix:
        profile_name += f"-{suffix}"
    upload_target = f"gs://chromeos-localmirror/distfiles/{profile_name}.xz"
    return [
        "gsutil.py",
        "cp",
        "-n",
        "-a",
        "public-read",
        profile_path,
        upload_target,
    ]


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
        "--chroot",
        default="llvm-next-pgo-chroot",
        help="""
        Name of the chroot to create. Will be recreated if it exists already.
        """,
    )
    parser.add_argument(
        "--clean-llvm",
        action="store_true",
        help="Allow the overwriting of any local changes to LLVM.",
    )
    parser.add_argument(
        "--profile-suffix",
        default="",
        help="""
        Suffix to add to the profile. Only meaningful if --upload is passed.
        """,
    )
    parser.add_argument(
        "--out-dir",
        default="llvm-next-pgo-chroot_out",
        help="""
        Name of the out/ directory to use. Will be recreated if it exists
        already.
        """,
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the profile after creation. Implies --compress.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="""
        Additionally put the uncompressed profile at the this path after
        creation.
        """,
    )
    parser.add_argument(
        "--branch",
        required=True,
        help="""
        Branch of LLVM that a PGO profile should be generated for. It's
        expected that the branch will have any patches applied already. Example
        branch is 'chromeos/llvm-r547379-1'.
        """,
    )

    return parser.parse_args(argv)


def get_rev_info_from_branch(branch_name: str) -> tuple[int, str]:
    # Note that `GetUpToDateReadOnlyLLVMRepo` prints helpful messages when it
    # goes to the network, so logging that this is happening here is redundant.
    llvm_repo = get_llvm_hash.GetCachedUpToDateReadOnlyLLVMRepo()

    sha = git_utils.resolve_ref(git_dir=llvm_repo.path, ref=branch_name)
    llvm_main = f"{git_utils.CROS_EXTERNAL_REMOTE}/{git_utils.CROS_MAIN_BRANCH}"
    merge_base = git_utils.merge_base(
        git_dir=llvm_repo.path,
        refs=[sha, llvm_main],
    )
    if not merge_base:
        raise ValueError(f"LLVM's main branch shares no history with {sha}?")
    rev = llvm_repo.GetRevisionFromHash(merge_base)
    return rev, sha


def main(argv: list[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    opts = parse_args(argv)
    pgo_utils.exit_if_in_chroot()

    rev, sha = get_rev_info_from_branch(opts.branch)
    logging.info(
        "Building PGO profile for %s (branched from upstream r%d)", sha, rev
    )

    if opts.chromiumos_tree:
        repo_root = chroot.FindChromeOSRootAbove(opts.chromiumos_tree)
    else:
        repo_root = chroot.FindChromeOSRootAboveToolchainUtils()

    logging.info("Repo root is %s", repo_root)

    logging.info("Creating new SDK")
    bootstrap_sdk_version = detect_bootstrap_sdk_version(repo_root)
    logging.info("Detected bootstrap SDK version: %s", bootstrap_sdk_version)
    bootstrap_chroot_info = ChrootInfo(
        opts.chroot, opts.out_dir, bootstrap_sdk_version
    )
    try:
        create_fresh_chroot(repo_root, bootstrap_chroot_info)
        chroot_profile_path = Path("/tmp/llvm-next-pgo-profile.prof")
        generate_pgo_profile(
            repo_root=repo_root,
            chroot_info=bootstrap_chroot_info,
            chroot_output_file=chroot_profile_path,
            sha=sha,
            clean_llvm=opts.clean_llvm,
        )
        profile_path = translate_chroot_path_to_out_of_chroot(
            repo_root, chroot_profile_path, bootstrap_chroot_info
        )
        if opts.output:
            shutil.copyfile(profile_path, opts.output)

        compressed_profile_path = compress_pgo_profile(profile_path)
        upload_command = determine_upload_command(
            compressed_profile_path, rev, opts.profile_suffix
        )
        friendly_upload_command = shlex.join(str(x) for x in upload_command)
        if opts.upload:
            logging.info(
                "Running `%s` to upload the profile...", friendly_upload_command
            )
            pgo_utils.run(upload_command)
        else:
            logging.info(
                "To upload the profile, run %r in %r",
                friendly_upload_command,
                repo_root,
            )
    except:
        logging.warning(
            "NOTE: Chroot left at %s and out dir is left at %s. "
            "If you don't plan to rerun this script, delete them.",
            bootstrap_chroot_info.chroot_name,
            bootstrap_chroot_info.out_dir_name,
        )
        raise
    else:
        logging.info(
            "Feel free to delete chroot %s and out dir %s when you're done "
            "with them.",
            bootstrap_chroot_info.chroot_name,
            bootstrap_chroot_info.out_dir_name,
        )
