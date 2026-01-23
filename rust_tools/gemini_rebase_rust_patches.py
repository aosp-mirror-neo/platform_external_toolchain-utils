# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Use Gemini to try to rebase Rust patches during an upgrade.

This script assumes that you've run `rust_uprev.py` and the current invocation
is stuck at generating the PGO profile, due to patches not applying. That means:
- `dev-lang/rust-bootstrap` passes,
- `dev-lang/rust-host` has been `cros-workon`'ed, and
- `dev-lang/rust-host` is failing in `src_prepare`.

It's recommended, but not required, that you `git commit` in
`chromiumos-overlay` prior to running this script, so you can more easily review
Gemini's changes.

Run this outside of the chroot.
"""

import argparse
import contextlib
import logging
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Generator

from cros_utils import cros_paths
from llvm_tools import chroot


# This is Gemini's repro script, which will be written to a file for Gemini to
# use. Key points:
#  - On success, print nothing that can potentially confuse Gemini, and just
#    exit cleanly. (Sometimes it may see a misc warning and get side-tracked. We
#    don't want that).
#  - On failure, trim to 100 lines of context. This should be _plenty_ to print
#    out the (short) patch errors that Portage produces, but is also a good
#    bound to keep Gemini focused. We don't want random Portage notes about
#    PGO/etc polluting context.
TRY_PATCH_SCRIPT_CONTENTS = r"""\
#!/bin/bash -u

x="$(cros_sdk --enter -- \
    bash -c 'sudo ebuild "$(equery w dev-lang/rust-host)" clean prepare' 2>&1)"

s=$?
if [[ "${s}" -eq 0 ]]; then
    echo "Patch success!"
    exit 0
fi

echo "FAILED. Last 100LOC of command output:"
tail -n100 <<< "${x}"
exit "${s}"
"""

# The name by which Gemini will invoke the above script.
TRY_PATCH_SCRIPT_NAME = "try-patch-rust.sh"

# Paths (relative to the CrOS checkout root) handed to Gemini. We assert these
# exist on every run.
RUST_HOST_PATCHES_PATH = (
    cros_paths.CHROMIUMOS_OVERLAY
    / "dev-lang"
    / "rust-host"
    / "files"
    / "cros-rustc"
)
RUST_HOST_9999_PATH = (
    cros_paths.CHROMIUMOS_OVERLAY
    / "dev-lang"
    / "rust-host"
    / "rust-host-9999.ebuild"
)
RUST_ECLASS_PATH = (
    cros_paths.CHROMIUMOS_OVERLAY / "eclass" / "cros-rustc.eclass"
)
RUST_PORTAGE_PATH = Path(
    "out",
    "sdk",
    "tmp",
    "portage",
    "dev-lang",
    "rust-host-9999",
    "work",
)


def assert_gemini_paths_exist(cros_checkout: Path) -> None:
    paths = (
        RUST_ECLASS_PATH,
        RUST_HOST_9999_PATH,
        RUST_HOST_PATCHES_PATH,
        RUST_PORTAGE_PATH,
    )
    for rel_path in paths:
        p = cros_checkout / rel_path
        if not p.exists():
            raise ValueError(f"No {p} found; the CrOS environment seems broken")


def generate_gemini_prompt(cros_checkout: Path) -> str:
    assert_gemini_paths_exist(cros_checkout)

    work_dir = cros_checkout / RUST_PORTAGE_PATH
    # We expect the source directory to be present if the user followed
    # instructions.
    # Use glob to find it.
    src_dirs = list(work_dir.glob("rustc-*-src"))
    if not src_dirs:
        raise ValueError(
            f"No rustc-*-src directory found in {work_dir}. "
            "Please ensure you have run rust_uprev.py and it failed at "
            "src_prepare."
        )

    if len(src_dirs) > 1:
        raise ValueError(
            f"Unexpected: multiple rustc-*-src directories found in "
            f"{work_dir}. "
            f"Results: {src_dirs}"
        )

    rust_src_dir = src_dirs[0]
    rust_src_path_relative = rust_src_dir.relative_to(cros_checkout)
    return f"""\
A Rust upgrade is in progress in this ChromiumOS tree. The patches are failing
to apply; it is your job to rebase them appropriately.

To test if patches apply, run `{TRY_PATCH_SCRIPT_NAME}`, which is on your PATH.
**Very often**, patches fail to apply for one of the following reasons:

1. A few irrelevant lines of context have been modified; in this case, the patch
   should be rebased.
2. The patch is no longer needed; in this case, the patch should be discarded.


**Notes**:

- This script will enter a ChromiumOS chroot; paths may not translate
  cleanly.
- The package being built is `dev-lang/rust-host-9999`. The ebuild for this
  is at `{RUST_HOST_9999_PATH}`.
- The `eclass` for this package contains most of the patch logic (and the
  primary patch list) is at `{RUST_ECLASS_PATH}`.
- The **unpacked** source tree for this package created by
  `{TRY_PATCH_SCRIPT_NAME}` will be at `{rust_src_path_relative}`.
- Each time `{TRY_PATCH_SCRIPT_NAME}` is run, the **entire** Rust source tree is
  deleted and recreated. Any changes you make *specifically to Rust sources*
  will be wiped out by this.


**Recommended flow**:

1. Run `{TRY_PATCH_SCRIPT_NAME}`.
2. Identify the failing patch (if any. If none, you are done.)
3. Run `git init` in `{rust_src_path_relative}`.
4. Run `git add` _only on specific files_ in the failing patch, and commit
   this. The commit message doesn't matter.
5. Apply the hunks of the failing patch to the Rust source tree.
6. If none of the hunks are still _logically_ applicable, remove the patch from
   the `PATCHES` list in the `{RUST_ECLASS_PATH}`. Otherwise, Use `git diff` on
   the repo, without committing intermediate state, to produce the new, rebased
   diff. Overwrite the existing patch file in `{RUST_HOST_PATCHES_PATH}` with
   it, **preserving any commentary at the top of the patch**.
7. Repeat until the patches all apply.
"""


@contextlib.contextmanager
def create_temp_bin_dir() -> Generator[Path, None, None]:
    """Creates a temporary dir to add to PATH for Gemini.

    This basically exists to put TRY_PATCH_SCRIPT_NAME on PATH. Gemini tends to
    do well when repro scripts are unambiguous and short.
    """
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        script_path = temp_dir / TRY_PATCH_SCRIPT_NAME
        script_path.write_text(TRY_PATCH_SCRIPT_CONTENTS, encoding="utf-8")
        script_path.chmod(0o755)
        yield temp_dir


def parse_opts(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--chromeos-tree",
        type=Path,
        help="""
        ChromeOS tree to make modifications in. Will be inferred if none is
        passed.
        """,
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="""
        Have Gemini enter interactive mode after it's done with its prompt.
        """,
    )
    opts = parser.parse_args(argv)

    if not opts.chromeos_tree:
        opts.chromeos_tree = chroot.FindChromeOSRootAboveToolchainUtils()

    return opts


def main(argv: list[str]) -> None:
    chroot.VerifyOutsideChroot()

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    opts = parse_opts(argv)
    cros_checkout = opts.chromeos_tree

    with create_temp_bin_dir() as temp_bin:
        env_with_temp_bin = dict(os.environ)
        env_with_temp_bin["PATH"] = f"{temp_bin}:" + env_with_temp_bin["PATH"]
        logging.info("Running Gemini...")
        prompt = generate_gemini_prompt(cros_checkout)

        gemini_cmd = ["gemini", "--yolo"]
        if opts.interactive:
            gemini_cmd.append("-i")
        gemini_cmd.append(prompt)

        subprocess.run(
            gemini_cmd,
            check=True,
            cwd=cros_checkout,
            env=env_with_temp_bin,
            encoding="utf-8",
        )

    logging.warning(
        "Gemini is done, meaning that `src_prepare()` likely passes now. "
        "**Please note** that the results should be verified by you. Generally "
        "Gemini does well with low-complexity rebases, but can always get "
        "things wrong."
    )
