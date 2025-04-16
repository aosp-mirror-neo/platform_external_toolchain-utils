# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Clones helper scripts into chromiumos-overlay.

Some files in here also need to live in chromiumos-overlay (e.g., the
patch_manager ones). This script simplifies the copying of those around.
"""

# Necessary until crbug.com/1006448 is fixed

import argparse
import os
from pathlib import Path
import shutil

from cros_utils import cros_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chromeos_path",
        help="Path to where CrOS' source tree lives. Will autodetect if you're "
        "running this from inside the CrOS source tree.",
    )
    args = parser.parse_args()

    my_dir = Path(os.path.abspath(os.path.dirname(__file__)))
    repo_root = args.chromeos_path
    if repo_root is None:
        repo_root = cros_paths.script_chromiumos_checkout_or_exit()

    chromiumos_overlay = repo_root / cros_paths.CHROMIUMOS_OVERLAY
    clone_files = [
        "failure_modes.py",
        "get_llvm_hash.py",
        "git_llvm_rev.py",
        "patch_manager.py",
        "subprocess_helpers.py",
    ]

    filesdir = chromiumos_overlay / "sys-devel/llvm/files/patch_manager"
    for f in clone_files:
        source = my_dir / f
        dest = filesdir / f
        print("%r => %r" % (source, dest))
        shutil.copyfile(source, dest)
