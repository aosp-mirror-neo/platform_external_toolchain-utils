#!/bin/bash -eu
# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

my_dir="$(dirname "$(readlink -m "$0")")"
cd "${my_dir}"

if [[ ! -e /etc/cros_chroot_version ]]; then
  echo "Re-exec'ing within the chroot..." >&2
  exec cros_sdk \
    --working-dir=. \
    -- \
    './run_python_tests.sh' \
    "$@"
fi

# Note that PYTHONPATH is not necessary here for regular execution, but a
# single script to run is specified, `pytest` will invoke it in a way that
# breaks toolchain-utils module importing.
#
# `-p no:hypothesispytest` is added because some chroots come with a
# 'hypothesis' pytest extension, which allows for property testing. We
# don't use that, and it autocreates a `.hypothesis/` subdir, which is
# slightly annoying.
#
# Exemptions:
# - git_llvm_rev_test is ignored because it takes a while. It'd be nice to
#   optionally enable it, but it's realistically very unlikely to break.
# - debug_info_test/debug_info_test.py is ignored, since that's the name of
#   a non-test script, and pytest is confused by this.
# - py/ just has symlinks to stuff back in toolchain-utils; no point in
#   checking that.
PYTHONPATH="${PWD}:${PYTHONPATH:-}" pytest \
  -p no:hypothesispytest \
  --ignore=debug_info_test/debug_info_test.py \
  --ignore=llvm_tools/llvm-project-copy \
  --ignore=llvm_tools/git_llvm_rev_test.py \
  --ignore=py/ \
  "$@"
