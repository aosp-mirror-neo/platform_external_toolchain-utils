# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities to create the first commit at the base of a new llvm branch.

Used primarily by new branch workflows and patch management tooling.
"""

from pathlib import Path
import re
import shutil

from cros_utils import git_utils
from llvm_tools import patch_utils


# This isn't a dict to prevent adding a tomli-w dependency.
PRESUBMIT_CFG_CONTENTS = """\
[Hook Overrides]
cros_license_check: True
long_line_check: True

[Hook Overrides Options]
cros_license_check: --exclude_regex=.*
long_line_check: --exclude_regex=.*
"""

CROS_DIR_README = """\
# CrOS Directory

This directory is used to store arbitrary changes for the ChromeOS Toolchain
team. Files in this directory are never meant to be upstreamed, and only
exist for local modification.

See src/third_party/toolchain-utils to see how this directory is configured.
"""

BASE_COMMIT_MESSAGE = """\
llvm-project: ChromeOS Base Commit

This is the LLVM ChromeOS Base Commit.

This commit marks the start of the ChromeOS patch branch. It introduces
the OWNERS file, and sets up the 'cros' directory for future use.

Functional patches for the ChromeOS LLVM Toolchain land after this
commit. This commit does not change how LLVM operates. The parent
commit to this change determines the LLVM synthetic revision.

BUG=None
TEST=CQ
"""


def make_base_commit(
    toolchain_utils_dir: Path,
    llvm_src_dir: Path,
    llvm_svn_revision: int,
    chromiumos_overlay: Path,
) -> None:
    """Create a commit which represents the base of a ChromeOS branch.

    Internally this is just `write_base_changes` followed by `commit_all`.
    """
    write_base_changes(
        toolchain_utils_dir, llvm_src_dir, llvm_svn_revision, chromiumos_overlay
    )
    git_utils.commit_all_changes(llvm_src_dir, BASE_COMMIT_MESSAGE)


def write_base_changes(
    toolchain_utils_dir: Path,
    llvm_src_dir: Path,
    llvm_svn_revision: int,
    chromiumos_overlay: Path,
) -> None:
    """Make changes which represents the base of a ChromeOS branch."""
    toolchain_utils_copy_files = (
        "OWNERS",
        "OWNERS.toolchain",
    )
    for copy_file in toolchain_utils_copy_files:
        shutil.copy(toolchain_utils_dir / copy_file, llvm_src_dir / copy_file)
    (llvm_src_dir / "PRESUBMIT.cfg").write_text(PRESUBMIT_CFG_CONTENTS)
    write_all_gentoo_cmake_hacks(llvm_src_dir, chromiumos_overlay)
    set_up_cros_dir(llvm_src_dir, llvm_svn_revision)


def set_up_cros_dir(llvm_src_dir: Path, llvm_svn_revision: int) -> None:
    """Create and init the llvm-project/cros directory."""
    cros_dir = llvm_src_dir / "cros"
    cros_dir.mkdir()
    readme = cros_dir / "README.md"
    readme.write_text(CROS_DIR_README)

    llvm_base_rev = cros_dir / "llvm-rev"
    llvm_base_rev.write_text(str(llvm_svn_revision))


def write_gentoo_cmake_hack(llvm_src_dir: Path, ebuild_dir: Path) -> None:
    """Modifies cmake files in LLVM so cmake.eclass doesn't modify them.

    Args:
        llvm_src_dir: Path to llvm-project git root we want to modify.
        ebuild_dir: Path to sys-devel/llvm Portage package directory.
    """
    # Upstream's `cmake.eclass` will try to override "dangerous" configurations
    # that override Gentoo settings. There's no way to skip this override, but
    # it _does_ have logic to detect if it has already run & skips all
    # modifications in that case. Since LLVM has no such "dangerous" settings,
    # and the `9999` ebuild never "goes live," it's safe to skip these.

    # The file to modify is the 'main' cmake file, which is determined based on
    # `CMAKE_USE_DIR`. Parsing that out isn't _too_ painful, so try it.
    ebuild_path = _find_ebuild_in_dir(ebuild_dir)
    ebuild_contents = ebuild_path.read_text(encoding="utf-8")
    cmake_use_dir_re = re.compile(
        # Use string concatenation rather than re.VERBOSE, since this regex
        # goes in an error message on failure, and that's _really_ hard to
        # read.
        r"^\s*"
        # While these all use `export`, it's not strictly required by
        # cmake.eclass.
        r"(?:export\s+)?" r'CMAKE_USE_DIR="\$\{S\}/([^"]+)"',
        re.MULTILINE,
    )
    cmake_use_dirs = cmake_use_dir_re.findall(ebuild_contents)
    if len(cmake_use_dirs) != 1:
        raise ValueError(
            f"Expected to find 1 match of {cmake_use_dir_re} in "
            f"{ebuild_path}; found {len(cmake_use_dirs)}"
        )

    cmake_file = llvm_src_dir / cmake_use_dirs[0] / "CMakeLists.txt"
    special_marker = "<<< Gentoo configuration >>>"
    if special_marker in cmake_file.read_text(encoding="utf-8"):
        return

    with cmake_file.open("a", encoding="utf-8") as f:
        f.write(
            f"\n# HACK from llvm_project_base_commit.py:\n# {special_marker}"
        )


def write_all_gentoo_cmake_hacks(
    llvm_src_dir: Path, chromiumos_overlay: Path
) -> None:
    """Writes gentoo cmake hacks for all known LLVM 9999 subprojects."""
    for subproject in patch_utils.CHROMEOS_LLVM_SUBPACKAGES:
        # N.B., this function will do nothing if the cmake file it aims to
        # modify has the marker already. Hence, calling it unconditionally on
        # all projects is fine.
        write_gentoo_cmake_hack(llvm_src_dir, chromiumos_overlay / subproject)


def _find_ebuild_in_dir(ebuild_dir: Path) -> Path:
    """Returns the path to a 9999 ebuild in `ebuild_dir`; raises if none."""
    candidates = list(ebuild_dir.glob("*-9999.ebuild"))
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one 9999 ebuild in {ebuild_dir}; found "
            f"{candidates}"
        )
    return candidates[0]
