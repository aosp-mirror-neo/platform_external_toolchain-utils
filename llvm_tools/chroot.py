# Copyright 2020 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Chroot helper functions."""

import os
from pathlib import Path

from cros_utils import cros_paths


def InChroot() -> bool:
    """Returns True if currently in the chroot."""
    return "CROS_WORKON_SRCROOT" in os.environ


def VerifyInsideChroot() -> None:
    """Checks whether the script invoked was executed in the chroot.

    Raises:
        AssertionError: The script was run outside the chroot.
    """
    assert InChroot(), "Script should be run inside the chroot."


def VerifyOutsideChroot() -> None:
    """Checks whether the script invoked was executed in the chroot.

    Raises:
        AssertionError: The script was run inside the chroot.
    """
    assert not InChroot(), "Script should be run outside the chroot."


def IsChromeOSRoot(path: Path) -> bool:
    """Returns True if `path` looks like the root of a CrOS checkout."""
    # This could technically be more stringent with checking, but realistically
    # should only be passed paths inside of CrOS trees.
    return (path / ".repo").exists()


def FindChromeOSRootAbove(chromeos_tree_path: Path) -> Path:
    """Returns the root of a ChromeOS tree, given a path in said tree.

    May return `chromeos_tree_path`, if that's already the root of the tree.

    Raises:
        ValueError if the given path is not in a ChromeOS tree.
    """
    if IsChromeOSRoot(chromeos_tree_path):
        return chromeos_tree_path

    for parent in chromeos_tree_path.parents:
        if IsChromeOSRoot(parent):
            return parent
    raise ValueError(f"{chromeos_tree_path} is not in a repo checkout")


def FindChromeOSRootAboveToolchainUtils() -> Path:
    """Returns the root of the ChromeOS tree that this checkout exists in.

    Raises:
        ValueError if this checkout is not in a ChromeOS tree.
    """
    result = cros_paths.script_chromiumos_checkout()
    if not result:
        raise ValueError("This script's checkout is not part of a CrOS tree.")
    return result
