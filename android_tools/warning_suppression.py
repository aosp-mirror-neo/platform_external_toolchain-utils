# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Miscellaneous utils for working with warning suppression."""

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
