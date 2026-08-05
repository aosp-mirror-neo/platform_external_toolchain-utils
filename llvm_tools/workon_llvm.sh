#!/bin/bash -eu
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Wrapper around chroot_toolchain.py to preserve the original interface.

my_dir="$(dirname "$(readlink -m "$0")")"

if [[ -z "${1:-}" || "$1" == "--help" || "$1" == "-h" ]]; then
  >&2 echo "Run cros workon start for all LLVM packages"
  >&2 echo
  >&2 echo "USAGE: $0 [-h|--help] BOARD"
  >&2 echo
  >&2 echo "  -h,--help:  Print this help."
  >&2 echo
  >&2 echo "  BOARD: the board to workon board-specific packages for. If"
  >&2 echo "    you'd only like to build host packages, pass '-' for this."
  exit 1
fi

board="$1"

echo "PLEASE NOTE: This script is deprecated and will be deleted."
echo "It's now a wrapper around another script; see command below"
set -x
if [[ "${board}" == "-" ]]; then
  exec "${my_dir}/../py/bin/llvm_tools/chroot_toolchain" workon --start --host
else
  exec "${my_dir}/../py/bin/llvm_tools/chroot_toolchain" \
    workon --start --host --board="${board}"
fi
