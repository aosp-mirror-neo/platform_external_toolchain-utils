# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Marks all LLVM packages as stable.

This essentially performs the job of annealing: take whatever's in the 9999
ebuilds, and put it in non-9999 ebuilds. The result is committed to
chromiumos-overlay, unless there are no changes to make. If the stabilization
does nothing, no new ebuilds will be created, and nothing will be committed.

The results of this script should _not_ be uploaded. Annealing should be
responsible for actually stabilizing our ebuilds upstream.

Run this from inside of the chroot.
"""

import argparse
import contextlib
import logging
import subprocess
import sys
from typing import List

from cros_utils import cros_paths
from llvm_tools import chroot
from llvm_tools import get_llvm_hash
from llvm_tools import manifest_utils
from llvm_tools import patch_utils


@contextlib.contextmanager
def llvm_checked_out_to(checkout_sha: str):
    """Checks out LLVM to `checkout_sha`, if necessary.

    Restores LLVM to the prior SHA when exited.
    """
    llvm_dir = cros_paths.CHROOT_SOURCE_ROOT / manifest_utils.LLVM_PROJECT_PATH
    original_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=llvm_dir,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout.strip()
    if checkout_sha == original_sha:
        logging.info(
            "LLVM is already checked out to %s; not checking out", checkout_sha
        )
        yield
        return

    return_code = subprocess.run(
        ["git", "status", "--porcelain"],
        check=False,
        cwd=llvm_dir,
    ).returncode
    if return_code:
        raise ValueError(
            f"LLVM checkout at {llvm_dir} is unclean; refusing to modify"
        )

    logging.info("Checking %s out to SHA %s...", llvm_dir, checkout_sha)

    subprocess.run(
        ["git", "checkout", checkout_sha],
        check=True,
        cwd=llvm_dir,
    )
    try:
        yield
    finally:
        logging.info("Restoring %s to original checkout...", llvm_dir)
        return_code = subprocess.run(
            ["git", "checkout", original_sha],
            check=False,
            cwd=llvm_dir,
        ).returncode
        if return_code:
            logging.error(
                "Failed checking llvm-project back out to %s :(",
                original_sha,
            )


def resolve_llvm_sha(llvm_next: bool) -> str:
    hash_obj = get_llvm_hash.LLVMHash()
    if llvm_next:
        return hash_obj.GetCrOSLLVMNextHash()
    return hash_obj.GetCrOSCurrentLLVMHash(cros_paths.CHROOT_SOURCE_ROOT)


def main(argv: List[str]) -> None:
    chroot.VerifyInsideChroot()

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
        "--llvm-next",
        action="store_true",
        help="""
        If passed, the ebuilds will be stabilized using the current llvm-next
        hash.
        """,
    )
    opts = parser.parse_args(argv)
    desired_sha = resolve_llvm_sha(opts.llvm_next)

    with llvm_checked_out_to(desired_sha):
        packages_to_stabilize = patch_utils.CHROMEOS_LLVM_SUBPACKAGES
        logging.info("Stabilizing %s...", ", ".join(packages_to_stabilize))

        cros_overlay = (
            cros_paths.CHROOT_SOURCE_ROOT / cros_paths.CHROMIUMOS_OVERLAY
        )
        return_code = subprocess.run(
            [
                "cros_mark_as_stable",
                f"--overlays={cros_overlay}",
                "--packages=" + ":".join(packages_to_stabilize),
                "commit",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
        ).returncode
        sys.exit(return_code)
