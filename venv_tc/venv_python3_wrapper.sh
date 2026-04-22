#!/bin/bash -eu
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# **NOTE**: If you opened a file in py/bin/... and got here, this is unlikely
# the droid you're looking for. Remove the `py/bin` components and add `.py` to
# the end. e.g., the implementation of `py/bin/llvm_tools/get_patch` is in
# `llvm_tools/get_patch.py`.
#
# Intended to be the symlink target for tools in `../py/bin`.
#
# This invokes `../venv_python3_wrapper.py` after ensuring that the appropriate
# python3 virtualenv has been set up.

my_dir="$(dirname "$(readlink -m "$0")")"
exec \
  "${my_dir}/venv_python3.sh" \
  "$(dirname "${my_dir}")/venv_python3_wrapper.py" \
  "$0" \
  "$@"
