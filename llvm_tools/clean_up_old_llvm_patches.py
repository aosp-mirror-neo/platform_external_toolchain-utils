# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Removes all LLVM patches before a certain point."""

import argparse
import importlib.abc
import importlib.util
import logging
from pathlib import Path
import re
import subprocess
import sys
import textwrap

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import patch_utils


# Folks who should be on the CC-line of any CLs that get uploaded.
CL_CC = ("gbiv@chromium.org",)


def remove_old_patches(toolchain_utils: Path, min_revision: int) -> bool:
    """Removes patches in cros_overlay. Returns whether changes were made."""
    patches_json = (
        toolchain_utils / cros_paths.DEFAULT_PATCHES_PATH_IN_TOOLCHAIN_UTILS
    )
    logging.info("GC'ing patches from %s...", patches_json)
    removed_patch_files = patch_utils.remove_old_patches(
        min_revision, patches_json
    )
    if not removed_patch_files:
        logging.info("No patches removed")
        return False

    for patch in removed_patch_files:
        logging.info("Removing %s...", patch)
        patch.unlink()
    return True


def commit_changes(cros_overlay: Path, min_rev: int):
    commit_msg = textwrap.dedent(
        f"""
        llvm: remove old patches

        These patches stopped applying before r{min_rev}, so should no longer
        be needed.

        BUG=b:332601837
        TEST=CQ
        """
    )

    subprocess.run(
        ["git", "commit", "--quiet", "-a", "-m", commit_msg],
        cwd=cros_overlay,
        check=True,
        stdin=subprocess.DEVNULL,
    )


def upload_changes(cros_overlay: Path, autosubmit_cwd: Path) -> None:
    cl_ids = git_utils.upload_to_gerrit(
        cros_overlay,
        remote="cros",
        branch="main",
        cc=CL_CC,
    )

    if len(cl_ids) > 1:
        raise ValueError(f"Unexpected: wanted just one CL upload; got {cl_ids}")

    cl_id = cl_ids[0]
    logging.info("Uploaded CL http://crrev.com/c/%s successfully.", cl_id)
    git_utils.set_autoreview_topic_and_labels(autosubmit_cwd, cl_id)


def find_chromeos_llvm_version(chromiumos_overlay: Path) -> int:
    sys_devel_llvm = chromiumos_overlay / "sys-devel" / "llvm"

    # Pick this from the name of the stable ebuild; 9999 is a bit harder to
    # parse, and stable is just as good.
    stable_llvm_re = re.compile(r"^llvm.*_pre(\d+)-r\d+\.ebuild$")
    match_gen = (
        stable_llvm_re.fullmatch(x.name) for x in sys_devel_llvm.iterdir()
    )
    matches = [int(x.group(1)) for x in match_gen if x]

    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one ebuild name match in {sys_devel_llvm}; "
            f"found {len(matches)}"
        )
    return matches[0]


def find_android_llvm_version(android_toolchain_tree: Path) -> int:
    android_version_py = (
        android_toolchain_tree
        / "toolchain"
        / "llvm_android"
        / "src"
        / "llvm_android"
        / "android_version.py"
    )

    # Per
    # https://docs.python.org/3/library/importlib.html#importing-a-source-file-directly.
    # Parsing this file is undesirable, since `_svn_revision`, as a variable,
    # isn't meant to be relied on. Let Python handle the logic instead.
    module_name = "android_version"
    android_version = sys.modules.get(module_name)
    if android_version is None:
        spec = importlib.util.spec_from_file_location(
            module_name, android_version_py
        )
        if not spec:
            raise ImportError(
                f"Failed loading module spec from {android_version_py}"
            )
        android_version = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = android_version
        loader = spec.loader
        if not isinstance(loader, importlib.abc.Loader):
            raise ValueError(
                f"Loader for {android_version_py} was of type "
                f"{type(loader)}; wanted an importlib.util.Loader"
            )
        loader.exec_module(android_version)

    rev = android_version.get_svn_revision()
    match = re.match(r"r(\d+)", rev)
    assert match, f"Invalid SVN revision: {rev!r}"
    return int(match.group(1))


def get_opts(argv: list[str]) -> argparse.Namespace:
    """Returns options for the script."""

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--android-toolchain",
        type=Path,
        help="""
        Path to an android-toolchain repo root. Only meaningful if
        `--autodetect-revision` is passed.
        """,
    )
    parser.add_argument(
        "--gerrit-tool-cwd",
        type=Path,
        help="""
        Working directory for `gerrit` tool invocations. This should point to
        somewhere within a ChromeOS source tree. If none is passed, this will
        try running them in the path specified by `--chromiumos-overlay`.
        """,
    )
    parser.add_argument(
        "--chromiumos-overlay",
        type=Path,
        help="""
        Path to chromiumos-overlay. Will autodetect if none is specified. If
        autodetection fails, none is specified, and this flag is needed (for
        minimum-revision autodetection), this script will fail.
        """,
    )
    parser.add_argument(
        "--toolchain-utils",
        type=Path,
        help="""
        Path to toolchain-utils. Will use the toolchain-utils executing this if
        none is specified.
        """,
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit changes after making them.",
    )
    parser.add_argument(
        "--upload-with-autoreview",
        action="store_true",
        help="""
        Upload changes after committing them. Implies --commit. Also adds
        default reviewers, and starts CQ+1 (among other convenience features).
        """,
    )

    revision_opt = parser.add_mutually_exclusive_group(required=True)
    revision_opt.add_argument(
        "--revision",
        type=int,
        help="""
        Revision to delete before (exclusive). All patches that stopped
        applying before this will be removed. Phrased as an int, e.g.,
        `--revision=1234`.
        """,
    )
    revision_opt.add_argument(
        "--autodetect-revision",
        action="store_true",
        help="""
        Autodetect the value for `--revision`. If this is passed, you must also
        pass `--android-toolchain`. This sets `--revision` to the _lesser_ of
        Android's current LLVM version, and ChromeOS'.
        """,
    )
    opts = parser.parse_args(argv)

    if not opts.toolchain_utils:
        opts.toolchain_utils = cros_paths.script_toolchain_utils_root()

    if not opts.gerrit_tool_cwd:
        opts.gerrit_tool_cwd = opts.chromiumos_overlay

    if opts.autodetect_revision:
        if not opts.chromiumos_overlay:
            maybe_cros_root = cros_paths.script_chromiumos_checkout()
            if not maybe_cros_root:
                parser.error(
                    "When using --autodetect-revision, this script must be "
                    "run from within a CrOS checkout unless you specify "
                    "--chromiumos-overlay."
                )
            opts.chromiumos_overlay = (
                maybe_cros_root / cros_paths.CHROMIUMOS_OVERLAY
            )

        if not opts.android_toolchain:
            parser.error(
                "--android-toolchain must be passed with --autodetect-revision"
            )

        cros_llvm_version = find_chromeos_llvm_version(opts.chromiumos_overlay)
        logging.info("Detected CrOS LLVM revision: r%d", cros_llvm_version)
        android_llvm_version = find_android_llvm_version(opts.android_toolchain)
        logging.info(
            "Detected Android LLVM revision: r%d", android_llvm_version
        )
        r = min(cros_llvm_version, android_llvm_version)
        logging.info("Selected minimum LLVM revision: r%d", r)
        opts.revision = r

    return opts


def main(argv: list[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    opts = get_opts(argv)
    toolchain_utils = opts.toolchain_utils
    gerrit_tool_cwd = opts.gerrit_tool_cwd
    upload = opts.upload_with_autoreview
    commit = opts.commit or upload
    min_revision = opts.revision

    made_changes = remove_old_patches(toolchain_utils, min_revision)
    if not made_changes:
        logging.info("No changes made; exiting.")
        return

    if not commit:
        logging.info(
            "Changes were made, but --commit wasn't specified. My job is done."
        )
        return

    logging.info("Committing changes...")
    commit_changes(toolchain_utils, min_revision)
    if not upload:
        logging.info("Change with removed patches has been committed locally.")
        return

    logging.info("Uploading changes...")
    upload_changes(toolchain_utils, gerrit_tool_cwd)
    logging.info("Change sent for review.")
