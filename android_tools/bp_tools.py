# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tools for working with Android.bp files."""

import logging
from pathlib import Path
import subprocess


_BPMODIFY_BIN = Path("out") / "host" / "linux-x86" / "bin"


def bpmodify_path(android_tree: Path) -> Path:
    """Returns the path to the `bpmodify` binary given an Android tree root."""
    return android_tree / _BPMODIFY_BIN / "bpmodify"


def bpfmt_path(android_tree: Path) -> Path:
    """Returns the path to the `bpfmt` binary given an Android tree root."""
    return android_tree / _BPMODIFY_BIN / "bpfmt"


def need_autobuild(android_tree: Path) -> bool:
    """Returns whether a build is needed to build `bp` tooling.

    This does not check for _all_ `bp` tooling, only the ones that this file
    provides an accessor for.
    """
    return not (
        bpmodify_path(android_tree).exists()
        and bpfmt_path(android_tree).exists()
    )


def autobuild_bp_tooling(android_tree: Path) -> None:
    """Builds `bp` tooling in the given Android tree."""
    result = subprocess.run(
        (
            "bash",
            "-c",
            ";".join(
                (
                    ". ./build/envsetup.sh",
                    "lunch aosp_cf_x86_64_phone-trunk_staging-eng",
                    "m blueprint_tools",
                )
            ),
        ),
        check=False,
        cwd=android_tree,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    if not result.returncode:
        logging.info("bmpodify build successful.")
        return

    logging.error("bp tooling build failed; stdout/stderr:\n%s", result.stdout)
    result.check_returncode()
