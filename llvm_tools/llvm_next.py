# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Contains useful constants for testing LLVM."""

from llvm_tools import cros_cls


LLVM_NEXT_HASH = "d4d2d7d7856258d5d781c4a912046fc7777122e2"
LLVM_NEXT_REV = 584947

# Group of people who are not in OWNERS, but can be trusted by bb_add.py and
# llvm_next_py_autoupdate.py if they're the uploader of CLs.
TRUSTED_UPLOADERS: tuple[str, ...] = ("devadharuns@google.com",)

# NOTE: Always specify patch-sets for these CLs. We don't want uploads by
# untrusted users to turn into bot invocations w/ untrusted input.
#
# Please note that these are (somewhat) automatically curated. See
# llvm_next_py_autoupdate.py.
_LLVM_NEXT_MANIFEST_CL: str | None = "https://crrev.com/i/9088380/3"
# These are CLs that need to run in llvm-next bot invocations that aren't
# uploaded by individuals in the global allowlist.
# pylint: disable=line-too-long
_LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = ()

# Users/tooling edit the strings above for ease-of-use; scripts should use the
# well-typed constants, though.
#
# Both of these require patch-sets for reasons in the `NOTE` above.
LLVM_NEXT_MANIFEST_CL: cros_cls.ChangeListURL | None = (
    cros_cls.ChangeListURL.parse_with_patch_set(_LLVM_NEXT_MANIFEST_CL)
    if _LLVM_NEXT_MANIFEST_CL
    else None
)

LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[cros_cls.ChangeListURL, ...] = tuple(
    cros_cls.ChangeListURL.parse_with_patch_set(url)
    for url in _LLVM_NEXT_TESTING_URL_ALLOWLIST
)
