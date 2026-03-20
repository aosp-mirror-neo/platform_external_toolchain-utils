# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for warning_exemption."""

import json
import textwrap
from unittest import mock

from llvm_tools import test_helpers
from llvm_tools import warning_exemption
import yaml  # pylint: disable=import-error


class Test(test_helpers.TempDirTestCase):
    """Tests for warning_exemption."""

    def test_yaml_round_trips(self) -> None:
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

    def test_package_parsing(self) -> None:
        with self.assertRaisesRegex(ValueError, ".*foo.*"):
            warning_exemption.Package.from_yaml("foo")

        # Shouldn't raise.
        warning_exemption.Package.from_yaml("foo/bar")

    def test_warning_scraping_works(self) -> None:
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
            warning_exemption.scrape_fatal_warnings_from_stdout(stdout),
            sorted(werrors),
        )

    def test_warning_scraping_normalization_works(self) -> None:
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
            warning_exemption.scrape_fatal_warnings_from_stdout(
                stdout, absolutize_with_cwd="/ROOT"
            ),
            sorted(werrors),
        )

    @mock.patch.object(warning_exemption, "scrape_fatal_warnings_from_stdout")
    def test_warning_file_parsing_works(self, mock_scrape: mock.Mock) -> None:
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
            warning_exemption.parse_fatal_warnings_file(tmpfile),
            (
                warning_exemption.Package(
                    category="category",
                    package_name="pkg-name",
                ),
                warning_exemption.FatalWarningGroup(
                    warning_names={"foo"},
                    warning_lines={"some error about [-Wfoo]"},
                ),
            ),
        )

    @mock.patch.object(warning_exemption, "scrape_fatal_warnings_from_stdout")
    def test_warning_file_parsing_handles_no_warnings(
        self, mock_scrape: mock.Mock
    ) -> None:
        mock_scrape.return_value = []

        tmpdir = self.make_tempdir()
        tmpfile = tmpdir / "warnings_report1234.json"
        warnings_file = {
            "cwd": "",
            "stdout": "",
        }
        with tmpfile.open("w", encoding="utf-8") as f:
            json.dump(warnings_file, f)

        self.assertIsNone(warning_exemption.parse_fatal_warnings_file(tmpfile))

    def test_warning_report_enumeration_works(self) -> None:
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
            sorted(warning_exemption.find_all_warning_reports_in(tmpdir)),
            sorted(warning_reports),
        )

    def test_removing_bash_style_works(self) -> None:
        self.assertEqual(
            warning_exemption.remove_bash_style_sequences(
                "\x1b[31mRed text.\x1b[0m"
            ),
            "Red text.",
        )
        self.assertEqual(
            warning_exemption.remove_bash_style_sequences("\x1b[31m"),
            "",
        )
        self.assertEqual(
            warning_exemption.remove_bash_style_sequences(
                "\x1b[1;32mBold green text.\x1b[0m"
            ),
            "Bold green text.",
        )
        self.assertEqual(
            warning_exemption.remove_bash_style_sequences(
                "Before reset.\x1bcAfter reset."
            ),
            "Before reset.After reset.",
        )
        # This sequence sets a _title_ for the terminal, and it looks like other
        # sequences do similar "global" operations. There's no reason to include
        # their inline text in the output.
        self.assertEqual(
            warning_exemption.remove_bash_style_sequences(
                "\x1b]0;Title\x07Line after title."
            ),
            "Line after title.",
        )
