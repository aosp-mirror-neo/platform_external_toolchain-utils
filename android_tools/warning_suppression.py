# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Miscellaneous utils for working with warning suppression."""

from pathlib import Path
import re

from android_tools import android_paths


# The 'helper CL' tags all HIDL builds with this flag.
HIDL_BUILD_MARKER_FLAG = "-D_ANDROID_HIDL_BUILD=1"
# Target name for HIDL's `cc_defaults`. This is specifically applied to all
# `cc_library`s that HIDL synthesizes.
HIDL_MODULE_DEFAULTS_TARGET_NAME = "hidl-module-defaults"
# The soong target for hidl-module-defaults.
HIDL_DEFAULTS_TARGET = (
    f"//{android_paths.BUILD_HIDL_SUBDIR}:{HIDL_MODULE_DEFAULTS_TARGET_NAME}"
)


TARGET_DIR_RE = re.compile(r"//([^:]+):")


def convert_target_to_android_bp(target: str) -> Path:
    """Infers an Android.bp file path from a target."""
    target_match = TARGET_DIR_RE.match(target)
    if not target_match:
        raise ValueError(f"Target {target!r} doesn't match {TARGET_DIR_RE}")

    # This match ends up being e.g., `bionic/libc` when given the target
    # `bionic/libc:libc`. `:libc` says "the libc target in the Android.bp
    # existing in `bionic/libc`.
    target_dir = Path(target_match.group(1))
    return target_dir / "Android.bp"
