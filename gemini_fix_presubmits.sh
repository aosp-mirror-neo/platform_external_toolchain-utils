#!/bin/bash -eu
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Runs Gemini, asking it to fix presubmits.

# Gemini should always execute at the root of toolchain-utils, so it has the
# ability to edit all files in it.
cd "$(dirname "$(readlink -m "$0")")"

cat - <<'EOF' | gemini --yolo
The presubmits for this repository are failing. Your job is to investigate
and fix them.

After presubmits pass, summarize your changes and exit. Leave the files
unstaged and uncommitted.

Presubmits can be run with:
```
py/bin/toolchain_utils_githooks/check-presubmit.py --infer_files --force_autofix
```
EOF

echo "** REMEMBER: Gemini may leave unstaged changes. Current 'git status':"
exec git status
