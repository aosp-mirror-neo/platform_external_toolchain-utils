# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A script used to lint patches in llvm_patches/.

Meant to be called from presubmit hooks in toolchain-utils.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import sys
from typing import List

from cros_utils import cros_paths
from llvm_tools import patch_utils


def load_patches_json(llvm_patches: Path) -> List[patch_utils.PatchEntry]:
    patches_json = (
        llvm_patches / cros_paths.DEFAULT_PATCHES_PATH_IN_TOOLCHAIN_UTILS.name
    )
    try:
        with patches_json.open(encoding="utf-8") as f:
            return patch_utils.json_to_patch_entries(
                workdir=llvm_patches, json_fd=f
            )
    except FileNotFoundError:
        sys.exit(f"error: PATCHES.json not found at {patches_json}")
    except json.JSONDecodeError:
        sys.exit(f"error: PATCHES.json at {patches_json} is ill-formed")


def extract_all_patch_paths_from_patches_json(
    patches_json: List[patch_utils.PatchEntry],
) -> List[Path]:
    return [x.workdir / x.rel_patch_path for x in patches_json]


def find_all_patch_files_in(base_dir: Path) -> List[Path]:
    results = []
    for root, _, files in os.walk(base_dir):
        proot = Path(root)
        for file in files:
            if file.endswith(".patch"):
                results.append(proot / file)
    return results


def main(argv: List[str]) -> None:
    toolchain_utils = cros_paths.script_toolchain_utils_root()

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # No args to parse for the moment, but responding to `--help` is nice.
    _ = parser.parse_args(argv)

    llvm_patches = (
        toolchain_utils
        / cros_paths.DEFAULT_PATCHES_PATH_IN_TOOLCHAIN_UTILS.parent
    )
    referenced_paths = set(
        extract_all_patch_paths_from_patches_json(
            load_patches_json(llvm_patches)
        )
    )
    available_paths = set(find_all_patch_files_in(llvm_patches))

    fail = False
    refed_but_not_available = referenced_paths - available_paths
    if refed_but_not_available:
        fail = True
        print(
            "Patches are referenced from PATCHES.json, but not present:",
            file=sys.stderr,
        )
        for p in sorted(refed_but_not_available):
            print(f"  - {p}", file=sys.stderr)

    available_but_not_refed = available_paths - referenced_paths
    if available_but_not_refed:
        fail = True
        print(
            "Patches are present, but not referenced from PATCHES.json:",
            file=sys.stderr,
        )
        for p in sorted(available_but_not_refed):
            print(f"  - {p}", file=sys.stderr)

    if fail:
        sys.exit(1)

    print(
        "All looks good! PATCHES.json parses, and present `.patch` files all "
        "correspond to it."
    )
