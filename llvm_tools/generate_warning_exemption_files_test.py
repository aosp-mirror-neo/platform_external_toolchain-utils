# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for generate_warning_exemption_files."""

import json
import textwrap
import unittest
from unittest import mock

# Rename this so the lines in this test aren't all super-long
from llvm_tools import generate_warning_exemption_files as gen
from llvm_tools import test_helpers
from llvm_tools import warning_exemption
import yaml  # pylint: disable=import-error


class Test(test_helpers.TempDirTestCase):
    """Tests for generate_warning_exemption_files."""

    def test_warning_scraping_works(self):
        stdout = textwrap.dedent(
            """\
            an error about flags:
            clang-2: error: flag -foo is not supported [-Wfoo,-Werror]

            another error about flags:
            error: unknown warning option [-Werror,-Wfoo2]

            an error about code:
            /path/to/foo.cc:12:34: error: don't do this [-Werror,-Wbar]

            a -Warning that's an error by default (thus lacks -Werror in
            brackets), e.g., b/409989901 and b/325463152.
            /path/to/foo.cc:12:34: error: don't do this either [-Wdefault-error]

            general non-Werror warning:
            /path/to/foo.cc:13:34: warning: fine, do this [-Wbaz]

            weird non-Werror warning with , in []:
            /path/to/foo.cc:14:34: warning: this is OK, too [-Wqux,-Wbaz]
            """
        )
        werrors = (
            (
                "clang-2: error: flag -foo is not supported [-Wfoo,-Werror]",
                "foo",
            ),
            ("error: unknown warning option [-Werror,-Wfoo2]", "foo2"),
            (
                "/path/to/foo.cc:12:34: error: don't do this [-Werror,-Wbar]",
                "bar",
            ),
            (
                "/path/to/foo.cc:12:34: error: don't do this either "
                "[-Wdefault-error]",
                "default-error",
            ),
        )
        self.assertEqual(
            gen.scrape_fatal_warnings_from_stdout(stdout),
            sorted(werrors),
        )

    def test_warning_scraping_normalization_works(self):
        stdout = textwrap.dedent(
            """\
            clang-2: error: flag -foo is not supported [-Wfoo]
            error: unknown warning option [-Wfoo2]
            /path/to/foo.cc:12:34: error: don't do this [-Wfoo3]
            ../foo.cc:12:34: error: don't do this either [-Wfoo4]
            foo.cc:12:34: error: don't do this either [-Wfoo5]
            """
        )
        werrors = (
            (
                "clang-2: error: flag -foo is not supported [-Wfoo]",
                "foo",
            ),
            ("error: unknown warning option [-Wfoo2]", "foo2"),
            (
                "/path/to/foo.cc:12:34: error: don't do this [-Wfoo3]",
                "foo3",
            ),
            (
                "/foo.cc:12:34: error: don't do this either [-Wfoo4]",
                "foo4",
            ),
            (
                "/ROOT/foo.cc:12:34: error: don't do this either [-Wfoo5]",
                "foo5",
            ),
        )
        self.assertEqual(
            gen.scrape_fatal_warnings_from_stdout(
                stdout, absolutize_with_cwd="/ROOT"
            ),
            sorted(werrors),
        )

    @mock.patch.object(gen, "scrape_fatal_warnings_from_stdout")
    def test_warning_file_parsing_works(self, mock_scrape):
        mock_scrape.return_value = [("some error about [-Wfoo]", "foo")]

        tmpdir = self.make_tempdir()
        tmpfile = tmpdir / "warnings_report1234.json"

        warnings_file = {
            # stdout is meaningless since we mock the scraping above.
            "cwd": "",
            "stdout": "",
            "parent_process_data": [
                {},
                {
                    "env": [],
                },
                {
                    "env": [
                        "CATEGORY=foo",
                    ],
                },
                {
                    "env": [
                        "PN=bar",
                    ],
                },
                {
                    "env": [
                        "CATEGORY=category",
                        "PN=pkg-name",
                    ],
                },
                {
                    "env": [
                        "CATEGORY=not-category",
                        "PN=not-pkg-name",
                    ],
                },
            ],
        }

        with tmpfile.open("w", encoding="utf-8") as f:
            json.dump(warnings_file, f)

        self.assertEqual(
            gen.parse_fatal_warnings_file(tmpfile),
            (
                warning_exemption.Package(
                    category="category",
                    package_name="pkg-name",
                ),
                gen.FatalWarningGroup(
                    warning_names={"foo"},
                    warning_lines={"some error about [-Wfoo]"},
                ),
            ),
        )

    @mock.patch.object(gen, "scrape_fatal_warnings_from_stdout")
    def test_warning_file_parsing_handles_no_warnings(self, mock_scrape):
        mock_scrape.return_value = []

        tmpdir = self.make_tempdir()
        tmpfile = tmpdir / "warnings_report1234.json"
        warnings_file = {
            "cwd": "",
            "stdout": "",
        }
        with tmpfile.open("w", encoding="utf-8") as f:
            json.dump(warnings_file, f)

        self.assertIsNone(gen.parse_fatal_warnings_file(tmpfile))

    def test_warning_report_enumeration_works(self):
        tmpdir = self.make_tempdir()
        warning_reports = (
            tmpdir / "foo" / "warnings_report1234.json",
            tmpdir / "bar" / "baz" / "qux" / "warnings_report1235.json",
        )
        not_warning_reports = (
            tmpdir / "foo" / "bar.json",
            tmpdir / "baz" / "warnings_report1234.json.in_progress",
            tmpdir / "qux",
        )

        for f in warning_reports + not_warning_reports:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.touch()

        self.assertEqual(
            sorted(gen.find_all_warning_reports_in(tmpdir)),
            sorted(warning_reports),
        )

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
                    gen.FatalWarningGroup(
                        warning_names={"bar", "foo"},
                        warning_lines=set(),
                    ),
                    {amd64_generic, brya},
                ),
                warning_exemption.Package(
                    category="dog",
                    package_name="pkg",
                ): (
                    gen.FatalWarningGroup(
                        warning_names={"baz"},
                        warning_lines=set(),
                    ),
                    {brya},
                ),
                warning_exemption.Package(
                    category="snek",
                    package_name="pkg",
                ): (
                    gen.FatalWarningGroup(
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
                    gen.FatalWarningGroup(
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

    def test_removing_bash_style_works(self):
        self.assertEqual(
            gen.remove_bash_style_sequences("\x1b[31mRed text.\x1b[0m"),
            "Red text.",
        )
        self.assertEqual(
            gen.remove_bash_style_sequences("\x1b[31m"),
            "",
        )
        self.assertEqual(
            gen.remove_bash_style_sequences(
                "\x1b[1;32mBold green text.\x1b[0m"
            ),
            "Bold green text.",
        )
        self.assertEqual(
            gen.remove_bash_style_sequences("Before reset.\x1bcAfter reset."),
            "Before reset.After reset.",
        )
        # This sequence sets a _title_ for the terminal, and it looks like other
        # sequences do similar "global" operations. There's no reason to include
        # their inline text in the output.
        self.assertEqual(
            gen.remove_bash_style_sequences(
                "\x1b]0;Title\x07Line after title."
            ),
            "Line after title.",
        )


if __name__ == "__main__":
    unittest.main()
