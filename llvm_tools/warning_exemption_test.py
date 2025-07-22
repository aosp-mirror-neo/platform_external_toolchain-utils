# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for warning_exemption."""

import unittest

from llvm_tools import warning_exemption
import yaml  # pylint: disable=import-error


class Test(unittest.TestCase):
    """Tests for warning_exemption."""

    def test_yaml_round_trips(self):
        yaml_input = warning_exemption.YamlFile(
            exemption_go_file_name="some_file.go",
            severe_warnings=[
                "severe_warning",
            ],
            per_package_warnings=[
                warning_exemption.YamlPackageWarnings(
                    package=warning_exemption.Package(
                        "per-package", "warnings"
                    ),
                    warning_lines=[
                        "per-package warning line 1",
                        "per-package warning line 2",
                    ],
                    warning_names=["per-package-name-1", "per-package-name-2"],
                    observed_on=[
                        warning_exemption.Builder(
                            name="per-package-builder-foo",
                            url="https://per-package-builder-foo",
                        ),
                    ],
                ),
            ],
            frozen_per_package_warnings=[
                warning_exemption.YamlPackageWarnings(
                    package=warning_exemption.Package("frozen", "warnings"),
                    warning_lines=[
                        "frozen warning line 1",
                        "frozen warning line 2",
                    ],
                    warning_names=["frozen-name-1", "frozen-name-2"],
                    observed_on=[
                        warning_exemption.Builder(
                            name="frozen-builder-foo",
                            url="https://frozen-builder-foo",
                        ),
                    ],
                ),
            ],
        )

        round_tripped = warning_exemption.YamlFile.from_yaml(
            yaml.safe_load(yaml_input.as_raw_yaml())
        )
        self.assertEqual(round_tripped, yaml_input)

    def test_package_parsing(self):
        with self.assertRaisesRegex(ValueError, ".*foo.*"):
            warning_exemption.Package.from_yaml("foo")

        # Shouldn't raise.
        warning_exemption.Package.from_yaml("foo/bar")


if __name__ == "__main__":
    unittest.main()
