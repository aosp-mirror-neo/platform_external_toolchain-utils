# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for generate_warning_exemption_files."""

import textwrap

# Rename this so the lines in this test aren't all super-long
from llvm_tools import generate_warning_exemption_files as gen
from llvm_tools import test_helpers
from llvm_tools import warning_exemption
import yaml  # pylint: disable=import-error


class Test(test_helpers.TempDirTestCase):
    """Tests for generate_warning_exemption_files."""

    ### Below are essentially Go-lden file tests.

    def test_go_file_creation_works_with_no_warnings(self):
        actual = gen.create_go_file(
            llvm_revision=123,
            per_package_warnings={},
        )
        fname = "warningSuppressionsForLLVM_r123"
        expected = gen.GO_COPYRIGHT_HEADER + textwrap.dedent(
            f"""\

            package main

            func {fname}(packageNameAndCategory string) []string {{
                return nil
            }}
            """
        )
        self.assertEqual(expected, actual)

    def test_go_file_creation_works_with_a_few_warnings(self):
        amd64_generic = warning_exemption.Builder(
            name="amd64-generic", url="https://amd64-generic-url"
        )
        brya = warning_exemption.Builder(name="brya", url="https://brya-url")
        actual = gen.create_go_file(
            llvm_revision=321,
            per_package_warnings={
                warning_exemption.Package(
                    category="cat",
                    package_name="pkg",
                ): (
                    warning_exemption.FatalWarningGroup(
                        warning_names={"bar", "foo"},
                        warning_lines=set(),
                    ),
                    {amd64_generic, brya},
                ),
                warning_exemption.Package(
                    category="dog",
                    package_name="pkg",
                ): (
                    warning_exemption.FatalWarningGroup(
                        warning_names={"baz"},
                        warning_lines=set(),
                    ),
                    {brya},
                ),
                warning_exemption.Package(
                    category="snek",
                    package_name="pkg",
                ): (
                    warning_exemption.FatalWarningGroup(
                        warning_names={"baz"},
                        warning_lines=set(),
                    ),
                    set(),
                ),
            },
        )
        fname = "warningSuppressionsForLLVM_r321"
        expected = gen.GO_COPYRIGHT_HEADER + textwrap.dedent(
            f"""\

            package main

            func {fname}(packageNameAndCategory string) []string {{
                switch packageNameAndCategory {{
                // Observed and suppressed on 2 builders during testing.
                // e.g., amd64-generic: https://amd64-generic-url.
                case "cat/pkg":
                    return []string{{ "-Wno-bar", "-Wno-foo" }}
                // Observed and suppressed on 1 builder during testing.
                // e.g., brya: https://brya-url.
                case "dog/pkg":
                    return []string{{ "-Wno-baz" }}
                // (No builder links were available for these exemptions).
                case "snek/pkg":
                    return []string{{ "-Wno-baz" }}
                default:
                    return nil
                }}
            }}
            """
        )
        self.assertEqual(expected, actual)

    def test_yaml_file_generation(self):
        amd64_generic = warning_exemption.Builder(
            name="amd64-generic",
            url="https://amd64-generic-url",
        )
        file_name = "foo.go"
        yaml_str = gen.create_yaml_file(
            file_name,
            per_package_warnings={
                warning_exemption.Package(
                    category="foo",
                    package_name="bar",
                ): (
                    warning_exemption.FatalWarningGroup(
                        warning_names={"foo", "bar"},
                        warning_lines={"Oh no [-Wfoo]", "Oh dear [-Wbar]"},
                    ),
                    {amd64_generic},
                ),
            },
        )

        print(yaml_str)
        per_package_warnings = [
            warning_exemption.YamlPackageWarnings(
                package=warning_exemption.Package("foo", "bar"),
                warning_lines=[
                    "Oh dear [-Wbar]",
                    "Oh no [-Wfoo]",
                ],
                warning_names=["bar", "foo"],
                observed_on=[amd64_generic],
            ),
        ]

        self.assertEqual(
            warning_exemption.YamlFile.from_yaml(yaml.safe_load(yaml_str)),
            warning_exemption.YamlFile(
                exemption_go_file_name=file_name,
                severe_warnings=["bar", "foo"],
                per_package_warnings=per_package_warnings,
                frozen_per_package_warnings=per_package_warnings,
            ),
        )

    def test_warning_path_canonicalization_works(self):
        result = gen.canonicalize_warning_lines(
            (
                "/build/brya/foo.cc:12:34: error: don't do this [-Wfoo2]",
                "/build/trogdor/foo.cc:12:34: error: don't do this [-Wfoo2]",
                "/path/to/foo.cc:12:34: error: don't do this [-Wfoo1]",
            )
        )
        expected_output = (
            "/build/BOARD/foo.cc:12:34: error: don't do this [-Wfoo2]",
            "/path/to/foo.cc:12:34: error: don't do this [-Wfoo1]",
        )
        self.assertEqual(result, sorted(expected_output))
