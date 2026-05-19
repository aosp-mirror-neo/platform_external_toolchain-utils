# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Contains useful constants for testing LLVM."""

from llvm_tools import cros_cls


LLVM_NEXT_HASH = "d4d2d7d7856258d5d781c4a912046fc7777122e2"
LLVM_NEXT_REV = 584947

# NOTE: Always specify patch-sets for CLs. We don't want uploads by untrusted
# users to turn into bot invocations w/ untrusted input.
#
# Please note that these are (somewhat) automatically curated. See
# llvm_next_py_autoupdate.py.
# pylint: disable=line-too-long
LLVM_NEXT_TESTING_CL_URLS: tuple[str, ...] = (
    "https://crrev.com/i/9088380/7",
    "https://chromium-review.git.corp.google.com/c/chromiumos/overlays/chromiumos-overlay/+/7649966/1",
)
# A list of CLs that constitute the current llvm-next roll.
# This is taken as the set of CLs that will be landed simultaneously in order
# to make llvm-next go live.
#
# Generally speaking, for simple rolls, this should just contain a link to the
# Manifest update CL, as well as (early on, at least) a link to a CL generated
# by upload_llvm_testing_helper_cl.py.
LLVM_NEXT_TESTING_CLS: tuple[cros_cls.ChangeListURL, ...] = tuple(
    cros_cls.ChangeListURL.parse(url) for url in LLVM_NEXT_TESTING_CL_URLS
)
