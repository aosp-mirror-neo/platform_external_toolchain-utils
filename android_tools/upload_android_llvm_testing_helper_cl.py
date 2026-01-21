# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Uploads an Android 'testing helper' CL.

At present, this just enables warning suppression in soong, though can be
extended to enable other behaviors that we wouldn't want to land in Android's
tree.

These CLs are never intended to be landed, and only intended to be used when
testing toolchain changes.
"""

import argparse
import logging
from pathlib import Path
import re
import subprocess
import sys
import textwrap

from android_tools import android_paths
from android_tools import bp_tools
from cros_utils import git_utils


_HIDL_MARKER_FLAG = "-D_ANDROID_HIDL_BUILD=1"

_ANDROID_INTERNAL_REMOTE = "goog"
_SUPPRESS_WARNING_FLAG = "-D_ANDROID_FORCE_DISABLE_WERROR=/dev/stdout"
_OPT_LEVEL_FLAG_RE = re.compile(r'^(\s+)"-O.",\s*$')


def generate_commit_message(repo_name: str) -> str:
    return f"""\
DO NOT COMMIT: android-llvm-testing helper CL ({repo_name})

This CL was automatically generated to facilitate LLVM testing.
The script that generated this is located at
external/toolchain-utils/android_tools/upload_llvm_testing_helper_cl.py.

Bug: None
Test: None
"""


def add_flag_after_optimization_level(file_contents: str, flag: str) -> str:
    """Adds the given flag after the optimization level in a Soong file.

    Nothing's particularly special about the optimization level in this case;
    it's just a common point to latch onto in Soong's `.go` files, and it
    happens to be unique in the cases we care about. The intent is "add it to
    global cflags for a specific class of builds."

    Raises:
        ValueError if the given file's contents does not have exactly one match
        for the expected optimization level, or if the given flag requires
        escaping.
    """
    if '"' in flag or "\\" in flag:
        # This can be supported, but it's not expected that any user will ever
        # want it, so just `raise` for now.
        raise ValueError("Flags requiring escaping aren't supported.")

    # The files this is called on are all Go files, written like
    # ```
    # // ...
    # var (
    #   cflags = []string{
    #     // comment
    #     "foo",
    #     // comment
    #     "-O2",
    #   }
    # )
    # ```
    # (or with the `cflags`-like variable bound in a function)
    new_lines = []
    num_matches = 0
    for line in file_contents.splitlines(keepends=True):
        new_lines.append(line)
        m = _OPT_LEVEL_FLAG_RE.fullmatch(line)
        if not m:
            continue

        num_matches += 1
        leading_space = m.group(1)
        new_lines.append(f'{leading_space}"{flag}",\n')

    if num_matches != 1:
        raise ValueError(
            f"Wanted exactly one match of {_OPT_LEVEL_FLAG_RE}; found "
            f"{num_matches}"
        )

    return "".join(new_lines)


def create_helper_cl_commit_in_worktree_of(soong_repo: Path, tot: bool) -> str:
    """Creates a commit containing the helper CL diff. Returns the SHA."""
    with git_utils.create_worktree(soong_repo) as worktree:
        if tot:
            git_utils.fetch_and_checkout(
                worktree,
                remote=_ANDROID_INTERNAL_REMOTE,
                branch="main",
            )

        files_to_modify = (
            worktree / "bpf" / "libbpf" / "libbpf_prog.go",
            worktree / "cc" / "config" / "global.go",
        )
        for f in files_to_modify:
            logging.info("Adding helper changes to %s...", f)
            contents = f.read_text(encoding="utf-8")
            new_contents = add_flag_after_optimization_level(
                contents, _SUPPRESS_WARNING_FLAG
            )
            f.write_text(new_contents, encoding="utf-8")

        logging.info("Running go fmt on modified files...")
        # Running `go fmt "${files[@]}"` actually doesn't work - `go fmt` will
        # complain that not all files are in the same directory. Running it
        # sequentially is good enough.
        for f in files_to_modify:
            subprocess.run(
                ("go", "fmt", f),
                check=True,
                stdin=subprocess.DEVNULL,
                cwd=worktree,
                encoding="utf-8",
            )
        return git_utils.commit_all_changes(
            worktree, generate_commit_message(repo_name="soong")
        )


def create_hidl_helper_cl_commit_in_worktree_of(
    android_tree: Path, hidl_repo: Path, tot: bool
) -> str:
    """Creates a commit containing the HIDL helper CL diff. Returns the SHA."""
    with git_utils.create_worktree(hidl_repo) as worktree:
        if tot:
            git_utils.fetch_and_checkout(
                worktree,
                remote=_ANDROID_INTERNAL_REMOTE,
                branch="main",
            )

        android_bp = worktree / "Android.bp"
        subprocess.run(
            (
                bp_tools.bpmodify_path(android_tree),
                "-w",
                "-m",
                "hidl-module-defaults",
                "-property",
                "cflags",
                "-add-literal",
                f'"{_HIDL_MARKER_FLAG}"',
                android_bp,
            ),
            check=True,
            stdin=subprocess.DEVNULL,
            cwd=worktree,
            encoding="utf-8",
        )

        subprocess.run(
            (bp_tools.bpfmt_path(android_tree), "-w", android_bp),
            check=True,
            stdin=subprocess.DEVNULL,
            cwd=worktree,
            encoding="utf-8",
        )
        return git_utils.commit_all_changes(
            worktree, generate_commit_message(repo_name="hidl")
        )


def main(argv: list[str]) -> None:
    """Main function."""
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
        "--android-tree",
        type=Path,
        help="""
        The root of the Android source tree to consult. Defaults to this
        script's tree, if it resides in an Android checkout.
        """,
    )
    parser.add_argument(
        "--autobuild",
        action="store_true",
        help="""
        If passed, needed tooling (e.g., bpmodify) will be automatically built
        by this script. This may mess with your out/ dir, so is not the default.
        This does nothing if `--hidl` is not specified.
        """,
    )
    parser.add_argument(
        "--no-upload",
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
    parser.add_argument(
        "--hidl",
        action="store_true",
        help="""
        Also upload a HIDL warning helper CL, which helps identify builds of
        HIDL-generated C/C++ files.
        """,
    )
    opts = parser.parse_args(argv)

    if opts.android_tree:
        android_paths.assert_is_valid_android_tree_root(
            parser, opts.android_tree
        )

    android_tree: Path | None = opts.android_tree
    if not android_tree:
        inferred_tree = android_paths.script_android_checkout()
        if not inferred_tree:
            parser.error(
                "Must run from inside an Android tree, or specify "
                "--android-tree"
            )
        android_tree = inferred_tree

    soong_repo = android_tree / android_paths.BUILD_SOONG_SUBDIR
    if not soong_repo.is_dir():
        raise ValueError(f"{soong_repo} is not a directory.")

    hidl_repo = android_tree / android_paths.BUILD_HIDL_SUBDIR
    if opts.hidl and not hidl_repo.is_dir():
        raise ValueError(f"{hidl_repo} is not a directory.")

    if opts.hidl and bp_tools.need_autobuild(android_tree):
        if not opts.autobuild:
            sys.exit(
                textwrap.dedent(
                    f"""\
                    No bpmodify/bpfmt binary and --autobuild not specified. This
                    tooling is needed for creating the hidl CL. Please either
                    build these in your android tree at {android_tree} via `m
                    blueprint_tools`, or pass --autobuild (though note that
                    `--autobuild` chooses its own lunch combo, so may mess with
                    your out/ dir).
                    """
                ).rstrip()
            )
        logging.info(
            "Automatically building bp tooling; this may take a few minutes..."
        )
        bp_tools.autobuild_bp_tooling(android_tree)

    logging.info("Creating soong commit...")
    helper_sha = create_helper_cl_commit_in_worktree_of(
        soong_repo, tot=opts.tot
    )

    hidl_sha = None
    if opts.hidl:
        logging.info("Creating hidl commit...")
        hidl_sha = create_hidl_helper_cl_commit_in_worktree_of(
            android_tree, hidl_repo, tot=opts.tot
        )

    if opts.no_upload:
        hidl_ext = ""
        if hidl_sha:
            hidl_ext = f", nor hidl commit ({hidl_sha})"
        logging.info(
            "--no-upload specified; not uploading new soong commit (%s)%s.",
            helper_sha,
            hidl_ext,
        )
        return

    logging.info("Uploading soong CL...")
    git_utils.upload_to_gerrit(
        git_repo=soong_repo,
        remote=_ANDROID_INTERNAL_REMOTE,
        branch="main",
        ref=helper_sha,
        wip=True,
    )

    if hidl_sha:
        logging.info("Uploading hidl CL...")
        git_utils.upload_to_gerrit(
            git_repo=hidl_repo,
            remote=_ANDROID_INTERNAL_REMOTE,
            branch="main",
            ref=hidl_sha,
            wip=True,
        )
