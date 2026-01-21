# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Paths and helpers common to Android source trees."""

import argparse
import functools
from pathlib import Path
import sys

from cros_utils import cros_paths


BUILD_SOONG_SUBDIR = Path("build") / "soong"

# Reexport this function for convenience.
script_toolchain_utils_root = cros_paths.script_toolchain_utils_root


@functools.cache
def script_android_checkout() -> Path | None:
    """Returns the absolute path to the Android checkout this script resides in.

    Returns None if this toolchain-utils checkout isn't part of an Android repo.
    """
    # toolchain-utils resides in external/toolchain-utils
    result = script_toolchain_utils_root().parent.parent
    if (result / ".repo").is_dir():
        return result
    return None


def script_android_checkout_or_exit() -> Path:
    """Returns the absolute path to the Android checkout this script resides in.

    Runs `sys.exit` with an appropriate error message if this isn't running in
    an Android checkout.
    """
    result = script_android_checkout()
    if not result:
        sys.exit(
            "This script must be invoked from a toolchain-utils checkout "
            "residing in an Android checkout."
        )
    return result


def is_android_tree_root(path: Path) -> bool:
    """Returns `True` if the given path seems like a root of an Android tree.

    This Android tree is specifically e.g., internal-main. This does not intend
    to match the toolchain branch's tree.
    """
    return (path / ".repo").exists() and (path / "Android.bp").exists()


def assert_is_valid_android_tree_root(
    parser: argparse.ArgumentParser, path: Path
) -> None:
    if not is_android_tree_root(path):
        parser.error(f"{path} is not a valid Android tree root.")
