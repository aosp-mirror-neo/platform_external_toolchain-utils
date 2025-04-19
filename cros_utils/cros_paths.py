# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A series of utilities for working with paths in ChromeOS."""

import functools
from pathlib import Path
import sys
from typing import Optional


# Paths to often-used directories from a CrOS root.
THIRD_PARTY_DIR = Path("src") / "third_party"

CHROMIUMOS_OVERLAY = THIRD_PARTY_DIR / "chromiumos-overlay"
LLVM_PROJECT = THIRD_PARTY_DIR / "llvm-project"
TOOLCHAINS_OVERLAY = THIRD_PARTY_DIR / "toolchains-overlay"
TOOLCHAIN_UTILS = THIRD_PARTY_DIR / "toolchain-utils"
TOOLCHAIN_UTILS_PYBIN = TOOLCHAIN_UTILS / "py" / "bin"

CHROOT_SOURCE_ROOT = Path("/mnt") / "host" / "source"

DEFAULT_LLVM_PKG_PATH = CHROMIUMOS_OVERLAY / "sys-devel" / "llvm"
DEFAULT_PATCHES_PATH_IN_TOOLCHAIN_UTILS = Path("llvm_patches") / "PATCHES.json"
DEFAULT_PATCHES_PATH = TOOLCHAIN_UTILS / DEFAULT_PATCHES_PATH_IN_TOOLCHAIN_UTILS


# Don't bind absolute paths to variables; functions are easier to mock.
#
# Functions that perform filesystem ops have results cached, since doing so is
# very cheap & the results should never change in production.


@functools.lru_cache(1)
def _script_path() -> Path:
    return Path(__file__).resolve()


def script_toolchain_utils_root() -> Path:
    """Returns the absolute path to the root of toolchain-utils/."""
    return _script_path().parent.parent


@functools.lru_cache(1)
def script_chromiumos_checkout() -> Optional[Path]:
    """Returns the absolute path to the CrOS checkout this script resides in.

    Returns None if this toolchain-utils checkout isn't part of a CrOS repo.
    """
    # toolchain-utils resides in src/third_party/toolchain-utils.
    result = script_toolchain_utils_root().parent.parent.parent
    if (result / ".repo").is_dir():
        return result
    return None


def script_chromiumos_checkout_or_exit() -> Path:
    """Returns the absolute path to the CrOS checkout this script resides in.

    Returns None if this toolchain-utils checkout isn't part of a CrOS repo.
    """
    result = script_chromiumos_checkout()
    if not result:
        sys.exit(
            "This script must be invoked from a toolchain-utils checkout "
            "residing in a ChromiumOS checkout."
        )
    return result
