#!/bin/bash -eu
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Until we can integrate with pytest, we have to use the virtual environment.

cd "$(dirname "$(readlink -m "$0")")"
venv="$(./establish_venv.sh)"
for x in *_test_venv.py; do
  "${venv}/bin/python3" "${x}"
done
