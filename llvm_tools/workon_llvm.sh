#!/bin/bash -eu

# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Enable cros workon for all the LLVM packages, and their cross-compilation
# variations.

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

CROSS_COMPILE_TARGETS=(
  cross-aarch64-cros-linux-gnu
  cross-arm-none-eabi
  cross-armv7a-cros-linux-gnueabihf
  cross-armv7m-cros-eabi
  cross-riscv32-cros-elf
  cross-x86_64-cros-linux-gnu
)

CROSS_COMPILE_PKGS=(
  libcxx
  llvm-libunwind
  compiler-rt
)

BOARD_PKGS=(
  sys-libs/libcxx
  sys-libs/llvm-libunwind
  sys-libs/compiler-rt
  sys-libs/scudo
  dev-util/lldb-server
)

workon_host() {
  local cross_compile_combination
  cross_compile_combination=()
  for target in "${CROSS_COMPILE_TARGETS[@]}"; do
    for pkg in "${CROSS_COMPILE_PKGS[@]}"; do
      cross_compile_combination+=("${target}/${pkg}")
    done
  done

  cros workon --host start \
    sys-devel/llvm \
    sys-libs/libcxx \
    sys-libs/llvm-libunwind \
    sys-libs/scudo \
    dev-util/lldb-server \
    "${cross_compile_combination[@]}"
}

workon_board() {
  local board="$1"
  cros workon -b "${board}" start "${BOARD_PKGS[@]}"
}

workon_host
echo "Set up host packages!"
board="$1"
if [[ "${board}" != "-" ]]; then
  workon_board "$1"
  echo "Set up packages for $1!"
fi
