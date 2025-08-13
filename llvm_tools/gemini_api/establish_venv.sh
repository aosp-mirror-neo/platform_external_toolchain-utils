#!/bin/bash -eu
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# This ensures a virtual environment exists for running the scripts in this
# directory in. It keeps separate ones for inside and outside of the chroot.
#
# Once it's finished, it prints the path to the virtual environment.
#
# Intended usage looks something like:
# ```bash
# venv="$(./establish_venv.sh)"
# "${venv}/bin/python3" ./check_reverts.py "${args[@]}"
# ```

cd "$(dirname "$(readlink -m "$0")")"

venv_dir="${PWD}"
if [[ -e /etc/cros_chroot_version ]]; then
  venv_dir+=/.chroot_venv
else
  venv_dir+=/.venv
fi

stamp="${venv_dir}/cros_setup_complete.stamp"
if [[ -e "${stamp}" ]]; then
  echo "${venv_dir}"
  exit
fi

rm -rf "${venv_dir}"
python3 -m venv "${venv_dir}" >/dev/null
"${venv_dir}/bin/python3" \
  -m pip \
  install \
  --disable-pip-version-check -r requirements.txt >/dev/null
touch "${stamp}"
echo "${venv_dir}"
