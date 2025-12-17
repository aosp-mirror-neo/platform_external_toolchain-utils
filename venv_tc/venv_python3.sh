#!/bin/bash -eu
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Runs the given command in a virtualenv, establishing the virtualenv if
# necessary.
#
# Usage:
# ./venv_python3.sh foo.py --bar baz

my_dir="$(dirname "$(readlink -m "$0")")"
if [[ -e /etc/cros_chroot_version ]]; then
  venv_name=.chroot_venv
else
  venv_name=.venv
fi

venv_location="${my_dir}/${venv_name}"
venv_stamp="${venv_location}/setup_stamp"
venv_requirements="${my_dir}/requirements.txt"

stamp_contents="### requirements.txt"
stamp_contents+=$'\n'"$(<"${venv_requirements}")"

is_venv_set_up() {
  [[ -e "${venv_stamp}" && "$(<"${venv_stamp}")" == "${stamp_contents}" ]]
}

set_up_venv_impl() {
  rm -rf "${venv_location}"

  python3 -m venv "${venv_location}"
  python3 "${my_dir}/wheels.py" ensure-downloaded
  "${venv_location}/bin/python3" -m ensurepip

  (cd "$(dirname "${venv_requirements}")" && \
    "${venv_location}/bin/python3" \
      -m \
      pip \
      install \
      -r \
      "$(basename "${venv_requirements}")" \
      "--no-index" \
      "--find-links" \
      "./wheels")
  echo -n "${stamp_contents}" > "${venv_stamp}"
}

set_up_venv() {
  # Capture the stdstreams of this, so it doesn't pollute the output of scripts
  # during initial setup. If setup fails, the streams can be dumped & we can
  # exit with a bad status.
  local stdstreams status

  # Note that `set -e` is disabled by entering conditionals, and
  # `set_up_venv_impl` should always execute with `set -e`.
  set +e
  stdstreams="$(set -e; set_up_venv_impl 2>&1)"
  status="$?"
  set -e

  if [[ "${status}" -ne 0 ]]; then
    echo "Setting up venv failed; stdout/stderr:" 2>&1
    echo "${stdstreams}" 2>&1
    exit 1
  fi
}

if ! is_venv_set_up; then
  # Grab a lock during setup only. It's expected that users won't be changing
  # requirements.txt concurrently with running Python scripts, but
  # they might want to run multiple python scripts that need to set up the
  # venv concurrently.
  venv_lock="${venv_location}.set-up-lock"
  exec 200> "${venv_lock}"
  flock -e 200
  # Double-check, since another process holding the lock might've just finished
  # setup.
  if ! is_venv_set_up; then
    set_up_venv
  fi
fi

exec "${venv_location}/bin/python3" "$@"
