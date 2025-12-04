# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Verifies that llvm_patches/PATCHES.json is well-formed."""

import collections
import unittest

from cros_utils import cros_paths
from llvm_tools import patch_utils


_PATCHES_JSON_PATH = (
    cros_paths.script_toolchain_utils_root()
    / cros_paths.DEFAULT_PATCHES_PATH_IN_TOOLCHAIN_UTILS
)


class VerifyLLVMPatchesTest(unittest.TestCase):
    """Verifies that llvm_patches/PATCHES.json is well-formed."""

    def test_patches_exist(self):
        """Verifies that all patches referenced in PATCHES.json exist."""
        patches_dir = _PATCHES_JSON_PATH.parent

        with _PATCHES_JSON_PATH.open(encoding="utf-8") as f:
            patch_entries = patch_utils.json_to_patch_entries(patches_dir, f)

        failures = []
        for entry in patch_entries:
            patch_path = entry.patch_path()
            if not patch_path.is_file():
                failures.append(
                    f"Patch file {patch_path} referenced in "
                    f"{_PATCHES_JSON_PATH} does not exist"
                )

        if failures:
            self.fail("\n\n".join(failures))

    def test_all_patches_referenced(self):
        """Verifies that all .patch files in are referenced by PATCHES.json."""
        patches_dir = _PATCHES_JSON_PATH.parent

        with _PATCHES_JSON_PATH.open(encoding="utf-8") as f:
            patch_entries = patch_utils.json_to_patch_entries(patches_dir, f)

        referenced_patches = {entry.rel_patch_path for entry in patch_entries}

        available_patches = set()
        for path in patches_dir.rglob("*.patch"):
            available_patches.add(str(path.relative_to(patches_dir)))

        unreferenced = available_patches - referenced_patches
        if unreferenced:
            unreferenced_str = "\n".join(
                sorted(str(patches_dir / x) for x in unreferenced)
            )
            self.fail(
                "The following patch files are present but not referenced in "
                f"PATCHES.json:\n{unreferenced_str}"
            )

    # TODO(b/466078369): This is an expected failure since PATCHES.json has two
    # entries for the same patch.
    @unittest.expectedFailure
    def test_no_overlapping_patches(self):
        """Verifies that entries pointing to the same patch do not overlap."""
        patches_dir = _PATCHES_JSON_PATH.parent

        with _PATCHES_JSON_PATH.open(encoding="utf-8") as f:
            patch_entries = patch_utils.json_to_patch_entries(patches_dir, f)

        # Group by patch file
        patches_by_file = collections.defaultdict(list)
        for entry in patch_entries:
            patches_by_file[entry.rel_patch_path].append(entry)

        failures = []

        for rel_path, entries in patches_by_file.items():
            if len(entries) <= 1:
                continue

            # Check for overlaps. This is technically n^2, but the number of
            # entries is expected to be in the single digits.
            for i, e1 in enumerate(entries):
                for e2 in entries[i + 1 :]:
                    range1 = e1.version_range or {}
                    from1 = range1.get("from") or 0
                    until1 = range1.get("until")
                    if until1 is None:
                        until1 = float("inf")

                    range2 = e2.version_range or {}
                    from2 = range2.get("from") or 0
                    until2 = range2.get("until")
                    if until2 is None:
                        until2 = float("inf")

                    if from1 < until2 and from2 < until1:
                        failures.append(
                            f"Patch {rel_path} has overlapping entries:\n"
                            f"  Entry 1: from {from1} until {until1}\n"
                            f"  Entry 2: from {from2} until {until2}"
                        )

        if failures:
            self.fail("\n\n".join(failures))

    def test_interval_order(self):
        """Verifies from < until on all relevant entries."""
        patches_dir = _PATCHES_JSON_PATH.parent

        with _PATCHES_JSON_PATH.open(encoding="utf-8") as f:
            patch_entries = patch_utils.json_to_patch_entries(patches_dir, f)

        failures = []
        for entry in patch_entries:
            version_range = entry.version_range or {}
            from_version = version_range.get("from")
            until_version = version_range.get("until")
            if from_version is None or until_version is None:
                continue

            if from_version > until_version:
                failures.append(
                    f"Patch {entry.rel_patch_path} has an invalid version "
                    f"range: 'from': {from_version}, 'until': "
                    f"{until_version}. 'from' must be less than 'until'."
                )
        if failures:
            self.fail("\n\n".join(failures))
