# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for venvless scripts' dependency graphs."""

import os
from pathlib import Path
import subprocess
import unittest

from cros_utils import cros_paths


class Test(unittest.TestCase):
    """Tests for venvless scripts."""

    def test_venvless_scripts_have_no_external_deps(self):
        toolchain_utils_root = cros_paths.script_toolchain_utils_root()
        py_bin = toolchain_utils_root / "py" / "bin"
        wrapper_path = toolchain_utils_root / "venvless_python3_wrapper.py"

        scripts_to_check = []

        # Walk recursively to find all symlinks in py/bin
        for root, _, files in os.walk(py_bin):
            root_path = Path(root)
            for file in files:
                file_path = root_path / file
                if not file_path.is_symlink():
                    continue

                target = file_path.resolve()
                if target == wrapper_path:
                    scripts_to_check.append(file_path)

        # Ensure we found something, otherwise the test is useless/broken
        self.assertTrue(scripts_to_check, "No venvless scripts found to test.")

        env = os.environ.copy()
        env["CROSTC_TEST_MODULE_IMPORTS"] = "1"

        failures = []
        for script in scripts_to_check:
            # Note that this is run in a subprocess to avoid `sys.modules`
            # pollution from this test (which is run by pytest, in the venv).
            result = subprocess.run(
                (script,),
                env=env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
            )

            if result.returncode:
                failures.append(
                    f"Script {script} failed dependency check:\n{result.stdout}"
                )

        if failures:
            self.fail("\n\n".join(failures))
