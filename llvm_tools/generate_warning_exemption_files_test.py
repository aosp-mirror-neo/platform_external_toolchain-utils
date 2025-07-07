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
        self.assertEqual(
            gen.scrape_fatal_warning_names_from_stdout(stdout),
            ["bar", "default-error", "foo", "foo2"],
        )

    @mock.patch.object(gen, "scrape_fatal_warning_names_from_stdout")
    def test_warning_file_parsing_works(self, mock_scrape):
        mock_scrape.return_value = ["foo"]

        tmpdir = self.make_tempdir()
        tmpfile = tmpdir / "warnings_report1234.json"

        warnings_file = {
            # stdout is meaningless since we mock the scraping above.
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
            [
                gen.FatalWarning(
                    warning_name="foo",
                    category="category",
                    package_name="pkg-name",
                )
            ],
        )

    @mock.patch.object(gen, "scrape_fatal_warning_names_from_stdout")
    def test_warning_file_parsing_handles_no_warnings(self, mock_scrape):
        mock_scrape.return_value = []

        tmpdir = self.make_tempdir()
        tmpfile = tmpdir / "warnings_report1234.json"
        warnings_file = {
            "stdout": "",
        }
        with tmpfile.open("w", encoding="utf-8") as f:
            json.dump(warnings_file, f)

        self.assertEqual(gen.parse_fatal_warnings_file(tmpfile), [])

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
        # Set maxDiff to a very large value, since tiny diffs are
        # borderline-useless given the size of these files.
        self.maxDiff = 10000
        actual = gen.create_go_file(
            llvm_revision=123,
            fatal_warnings={},
        )
        fname = "getWarningsForLLVM_r123"
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
        # Set maxDiff to a very large value, since tiny diffs are
        # borderline-useless given the size of these files.
        self.maxDiff = 10000
        amd64_generic = gen.Builder(
            name="amd64-generic", url="https://amd64-generic-url"
        )
        brya = gen.Builder(name="brya", url="https://brya-url")
        actual = gen.create_go_file(
            llvm_revision=321,
            fatal_warnings={
                gen.FatalWarning(
                    warning_name="foo",
                    category="cat",
                    package_name="pkg",
                ): [brya],
                gen.FatalWarning(
                    warning_name="bar",
                    category="cat",
                    package_name="pkg",
                ): [amd64_generic],
                gen.FatalWarning(
                    warning_name="baz",
                    category="dog",
                    package_name="pkg",
                ): [brya],
                gen.FatalWarning(
                    warning_name="baz",
                    category="snek",
                    package_name="pkg",
                ): [],
            },
        )
        fname = "getWarningsForLLVM_r321"
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


if __name__ == "__main__":
    unittest.main()
