# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for parse_and_apply_warning_exemptions."""

import json
from pathlib import Path
import textwrap
from typing import Any
import unittest
from unittest import mock

from android_tools import parse_and_apply_warning_exemptions as pa
from android_tools import warning_suppression
from llvm_tools import test_helpers
from llvm_tools import warning_exemption


EXAMPLE_OUT_FILE = (
    "out/soong/.intermediates/bionic/libc/libc_bionic/"
    "android_x86_silvermont_static_apex10000_sabi/obj/bionic/libc/bionic/"
    "sigprocmask.o"
)

# N.B., this is a pretty-printed warning report with some of the cruft removed.
# They don't appear pretty-printed in logs, but that's fine.
EXAMPLE_WARNING_REPORT = r"""
{
  "cwd": "/b/f/w",
  "command": [
    "prebuilts/clang/host/linux-x86/clang-r563880c/bin/clang++-real",
    "-c",
    "-MD",
    "-MF",
    "out/soong/.intermediates/bionic/libc/libc_bionic/android_x86_64_silvermont_static/obj/bionic/libc/bionic/sigprocmask.o.d",
    "-o",
    "out/soong/.intermediates/bionic/libc/libc_bionic/android_x86_64_silvermont_static/obj/bionic/libc/bionic/sigprocmask.o",
    "bionic/libc/bionic/sigprocmask.cpp"
  ],
  "stdout": "\u001b[1mbionic/libc/bionic/sigprocmask.cpp:46:55: \u001b[0m\u001b[0;1;31merror: \u001b[0m\u001b[1mGCC does not allow '__noinline__' attribute in this position on a function definition [-Werror,-Wgcc-compat]\u001b[0m\n   46 |sigset64_t* old_set) \u001b[0;34m__attribute__\u001b[0m((__noinline__)) {\u001b[0m\n      | \u001b[0;1;32m                                        ^\n\u001b[0m1 error generated.\n",
  "parent_process_data": []
}
"""


def example_warning_report_expected_result() -> (
    warning_exemption.FatalWarningGroup
):
    """Returns the expected FatalWarningGroup for EXAMPLE_WARNING_REPORT."""
    return warning_exemption.FatalWarningGroup(
        warning_names={"gcc-compat"},
        warning_lines={
            "bionic/libc/bionic/sigprocmask.cpp:46:55: error: GCC does "
            "not allow '__noinline__' attribute in this position on a "
            "function definition [-Werror,-Wgcc-compat]"
        },
    )


class TestInferTargetFromCmdline(test_helpers.TempDirTestCase):
    """Tests for parse_and_apply_warning_exemptions."""

    def test_infer_target_from_cmdline_success(self) -> None:
        cmd = [
            "clang",
            "-o",
            EXAMPLE_OUT_FILE,
            "bionic/libc/bionic/sigprocmask.c",
        ]
        self.assertEqual(
            pa.infer_target_from_cmdline(cmd),
            "//bionic/libc:libc_bionic",
        )

    def test_infer_target_from_cmdline_no_dash_o(self) -> None:
        cmd = [
            "clang",
            "bionic/libc/bionic/sigprocmask.c",
        ]
        self.assertIsNone(pa.infer_target_from_cmdline(cmd))

    def test_infer_target_from_cmdline_no_soong_prefix(self) -> None:
        cmd = [
            "clang",
            "-o",
            EXAMPLE_OUT_FILE.replace("/soong/", "/not-soong/", 1),
            "bionic/libc/bionic/sigprocmask.c",
        ]
        self.assertIsNone(pa.infer_target_from_cmdline(cmd))

    def test_infer_target_from_cmdline_no_obj_dir(self) -> None:
        cmd = [
            "clang",
            "-o",
            EXAMPLE_OUT_FILE.replace("/obj/", "/", 1),
            "bionic/libc/bionic/sigprocmask.c",
        ]
        self.assertIsNone(pa.infer_target_from_cmdline(cmd))

    def test_infer_target_from_cmdline_hidl(self) -> None:
        cmd = [
            "clang",
            "-o",
            EXAMPLE_OUT_FILE,
            "some/hidl/file.cpp",
            warning_suppression.HIDL_BUILD_MARKER_FLAG,
        ]
        self.assertEqual(
            pa.infer_target_from_cmdline(cmd),
            warning_suppression.HIDL_DEFAULTS_TARGET,
        )


class TestParseOneWarningReport(test_helpers.TempDirTestCase):
    """Tests for parse_one_warning_report."""

    def test_parse_one_warning_report_success(self) -> None:
        parse_result = pa.parse_one_warning_report(
            EXAMPLE_WARNING_REPORT, report_line_number=1
        )
        # Use `assert` to appease mypy.
        assert parse_result, "Parsing warning report failed unexpectedly"
        target, warnings = parse_result
        self.assertEqual(target, "//bionic/libc:libc_bionic")
        self.assertEqual(warnings, example_warning_report_expected_result())


class TestParseWarningReports(test_helpers.TempDirTestCase):
    """Tests for parse_warning_reports."""

    def test_parse_warning_reports_success(self) -> None:
        json_content = EXAMPLE_WARNING_REPORT.replace("\n", " ")
        log_path = self.make_tempdir() / "build.log"
        log_path.write_text(
            (
                "some text beforehand\n\n"
                "<LLVM_NEXT_ERROR_REPORT>"
                f"{json_content}"
                "</LLVM_NEXT_ERROR_REPORT>\n"
                "some text after\n"
            ),
            encoding="utf-8",
        )

        result = pa.parse_warning_reports(log_path)

        parsed_result = example_warning_report_expected_result()
        self.assertEqual(
            result,
            {"//bionic/libc:libc_bionic": parsed_result},
        )

    def test_parse_warning_reports_with_invalid_utf8(self) -> None:
        json_content = EXAMPLE_WARNING_REPORT.replace("\n", " ")
        log_path = self.make_tempdir() / "build.log"
        log_path.write_bytes(
            b"some text beforehand\n"
            # A line with invalid UTF-8.
            b"\x80\n"
            b"<LLVM_NEXT_ERROR_REPORT>"
            + json_content.encode("utf-8")
            + b"</LLVM_NEXT_ERROR_REPORT>\n"
            b"some text after\n"
        )

        result = pa.parse_warning_reports(log_path)

        parsed_result = example_warning_report_expected_result()
        self.assertEqual(
            result,
            {"//bionic/libc:libc_bionic": parsed_result},
        )

    def test_parse_warning_reports_with_interesting_invalid_utf8(self) -> None:
        log_path = self.make_tempdir() / "build.log"
        # Skip error reports with invalid utf-8.
        invalid_utf8_line = (
            b"<LLVM_NEXT_ERROR_REPORT>\x80</LLVM_NEXT_ERROR_REPORT>\n"
        )
        log_path.write_bytes(invalid_utf8_line)
        result = pa.parse_warning_reports(log_path)
        self.assertEqual(result, {})


class TestGroupTargetsByBpFile(test_helpers.TempDirTestCase):
    """Tests for group_targets_by_bp_file."""

    def test_group_targets_by_bp_file_success(self) -> None:
        targets = [
            "//bionic/libc:libc_bionic",
            "//system/core:libutils",
            "//bionic/libc:libc_bionic_ndk",
        ]
        expected = {
            Path("bionic/libc/Android.bp"): [
                ("libc_bionic", "//bionic/libc:libc_bionic"),
                ("libc_bionic_ndk", "//bionic/libc:libc_bionic_ndk"),
            ],
            Path("system/core/Android.bp"): [
                ("libutils", "//system/core:libutils")
            ],
        }
        result = pa.group_targets_by_bp_file(targets)
        # Sort these for consistent ordering.
        for v in result.values():
            v.sort()
        for v in expected.values():
            v.sort()
        self.assertEqual(result, expected)

    def test_group_targets_by_bp_file_empty_input(self) -> None:
        self.assertEqual(pa.group_targets_by_bp_file([]), {})

    def test_group_targets_by_bp_file_invalid_target(self) -> None:
        with self.assertRaises(ValueError):
            pa.group_targets_by_bp_file(["//bionic/libc"])


class TestUpdateHunkHeaderForAddedLines(test_helpers.TempDirTestCase):
    """Tests for update_hunk_header_for_added_lines."""

    def test_update_hunk_header_docstring_example(self) -> None:
        header = "@@ -5,12 +5,18 @@"
        new_header = pa.update_hunk_header_for_added_lines(
            header, added_lines=2, preexisting_added_lines=4
        )
        self.assertEqual(new_header, "@@ -5,12 +9,20 @@")

    def test_update_hunk_header_no_changes(self) -> None:
        header = "@@ -1,1 +1,1 @@"
        new_header = pa.update_hunk_header_for_added_lines(
            header, added_lines=0, preexisting_added_lines=0
        )
        self.assertEqual(new_header, "@@ -1,1 +1,1 @@")

    def test_update_hunk_header_with_added_lines(self) -> None:
        header = "@@ -10,5 +10,5 @@"
        new_header = pa.update_hunk_header_for_added_lines(
            header, added_lines=3, preexisting_added_lines=0
        )
        self.assertEqual(new_header, "@@ -10,5 +10,8 @@")

    def test_update_hunk_header_invalid_header(self) -> None:
        with self.assertRaises(ValueError):
            pa.update_hunk_header_for_added_lines("invalid header", 1, 1)


class TestAddSuppressionCommentsToDiff(test_helpers.TempDirTestCase):
    """Tests for add_suppression_comments_to_diff."""

    def test_add_suppression_comments_to_diff_multiple_hunks(self) -> None:
        diff = textwrap.dedent(
            """
            --- a/Android.bp
            +++ b/Android.bp
            @@ -1,2 +1,3 @@
             cc_library {
                 name: "libfoo",
            +    cflags: ["-Wno-foo"],
             }
            @@ -10,3 +11,4 @@
             cc_library {
                 name: "libbar",
            +    cflags: ["-Wno-bar"],
             } """
        )
        expected_diff = textwrap.dedent(
            """
            --- a/Android.bp
            +++ b/Android.bp
            @@ -1,2 +1,4 @@
             cc_library {
                 name: "libfoo",
            +// Bulk-suppressed; see b/12345 for details
            +    cflags: ["-Wno-foo"],
             }
            @@ -10,3 +12,5 @@
             cc_library {
                 name: "libbar",
            +// Bulk-suppressed; see b/12345 for details
            +    cflags: ["-Wno-bar"],
             } """
        )
        result = pa.add_suppression_comments_to_diff(
            12345, diff, ['"-Wno-foo"', '"-Wno-bar"']
        )
        self.assertEqual(result, expected_diff)


class TestExemptionSummary(test_helpers.TempDirTestCase):
    """Tests for ExemptionSummary."""

    def test_from_file_success(self) -> None:
        temp_dir = self.make_tempdir()
        summary_file = temp_dir / "summary.json"
        summary_content: dict[str, Any] = {
            "bug_number": 12345,
            "exemptions": {
                "/foo/bar": {
                    "updated_files": {
                        "foo/bar/Android.bp": {
                            "per_target_warnings": {
                                "//foo:bar": ["unused-variable"]
                            }
                        }
                    },
                },
                "/baz/qux": {
                    "updated_files": {
                        "baz/qux/Android.bp": {
                            "per_target_warnings": {
                                "//baz:qux": ["unused-function"]
                            }
                        }
                    },
                },
            },
        }
        with summary_file.open("w", encoding="utf-8") as f:
            json.dump(summary_content, f)

        summary = pa.ExemptionSummary.from_file(summary_file)

        self.assertEqual(
            summary,
            pa.ExemptionSummary(
                bug_number=12345,
                exemptions={
                    "/foo/bar": pa.RepoExemptionSummary(
                        updated_files={
                            "foo/bar/Android.bp": pa.BpExemptionSummary(
                                per_target_warnings={
                                    "//foo:bar": ["unused-variable"]
                                }
                            )
                        },
                    ),
                    "/baz/qux": pa.RepoExemptionSummary(
                        updated_files={
                            "baz/qux/Android.bp": pa.BpExemptionSummary(
                                per_target_warnings={
                                    "//baz:qux": ["unused-function"]
                                }
                            )
                        },
                    ),
                },
            ),
        )

    def test_from_file_empty(self) -> None:
        temp_dir = self.make_tempdir()
        summary_file = temp_dir / "summary.json"
        summary_content: dict[str, Any] = {
            "bug_number": 123,
            "exemptions": {},
        }
        with summary_file.open("w", encoding="utf-8") as f:
            json.dump(summary_content, f)

        summary = pa.ExemptionSummary.from_file(summary_file)

        self.assertEqual(
            summary,
            pa.ExemptionSummary(
                bug_number=123,
                exemptions={},
            ),
        )

    def test_uploaded_cls_round_trip(self) -> None:
        temp_dir = self.make_tempdir()
        summary_file = temp_dir / "summary.json"
        summary = pa.ExemptionSummary(
            bug_number=12345,
            exemptions={
                "/foo/bar": pa.RepoExemptionSummary(
                    updated_files={
                        "foo/bar/Android.bp": pa.BpExemptionSummary(
                            per_target_warnings={
                                "//foo:bar": ["unused-variable"]
                            }
                        )
                    },
                    uploaded_cl="ag/12345",
                )
            },
        )

        summary.write_to_file(summary_file)

        read_summary = pa.ExemptionSummary.from_file(summary_file)
        self.assertEqual(read_summary, summary)

    def test_write_to_file(self) -> None:
        temp_dir = self.make_tempdir()
        summary_file = temp_dir / "summary.json"
        summary = pa.ExemptionSummary(
            bug_number=987,
            exemptions={
                "/foo/bar": pa.RepoExemptionSummary(
                    updated_files={
                        "foo/bar/Android.bp": pa.BpExemptionSummary(
                            per_target_warnings={
                                "//foo:bar": ["unused-variable"]
                            }
                        )
                    }
                ),
                "/baz/qux": pa.RepoExemptionSummary(
                    updated_files={
                        "baz/qux/Android.bp": pa.BpExemptionSummary(
                            per_target_warnings={
                                "//baz:qux": ["unused-function"]
                            }
                        )
                    }
                ),
            },
        )

        summary.write_to_file(summary_file)

        read_summary = pa.ExemptionSummary.from_file(summary_file)

        self.assertEqual(read_summary, summary)


class TestPopulateAndWriteSummary(test_helpers.TempDirTestCase):
    """Tests for populate_and_write_summary."""

    def test_populate_and_write_summary(self) -> None:
        summary_file = self.make_tempdir() / "summary.json"
        bug_number = 12345
        updated_targets = {
            "//foo/bar:bar": ["unused-variable"],
            "//baz/qux:qux": ["unused-function"],
        }
        uploaded_cls = {
            Path("foo/bar"): "ag/111",
            Path("baz/qux"): "ag/222",
        }
        file_to_repo = {
            Path("foo/bar/Android.bp"): Path("foo/bar"),
            Path("baz/qux/Android.bp"): Path("baz/qux"),
        }

        pa.populate_and_write_summary(
            bug_number,
            summary_file,
            updated_targets,
            uploaded_cls,
            file_to_repo,
        )

        summary = pa.ExemptionSummary.from_file(summary_file)
        expected_summary = pa.ExemptionSummary(
            bug_number=bug_number,
            exemptions={
                "foo/bar": pa.RepoExemptionSummary(
                    updated_files={
                        "foo/bar/Android.bp": pa.BpExemptionSummary(
                            per_target_warnings={
                                "//foo/bar:bar": ["unused-variable"]
                            }
                        )
                    },
                    uploaded_cl="ag/111",
                ),
                "baz/qux": pa.RepoExemptionSummary(
                    updated_files={
                        "baz/qux/Android.bp": pa.BpExemptionSummary(
                            per_target_warnings={
                                "//baz/qux:qux": ["unused-function"]
                            }
                        )
                    },
                    uploaded_cl="ag/222",
                ),
            },
        )
        self.assertEqual(summary, expected_summary)


class TestExtractCoreWarningName(unittest.TestCase):
    """Tests for extract_core_warning_name."""

    def test_extract_core_warning_name(self) -> None:
        self.assertEqual(pa.extract_core_warning_name("-Wno-foo"), "foo")
        self.assertEqual(pa.extract_core_warning_name("-Wfoo"), "foo")
        self.assertEqual(pa.extract_core_warning_name("-Werror=foo"), "foo")
        self.assertEqual(pa.extract_core_warning_name("-Wno-error=foo"), "foo")
        self.assertEqual(pa.extract_core_warning_name("foo"), "foo")


class TestFormatExemptionCommitMessage(unittest.TestCase):
    """Tests for format_exemption_commit_message."""

    def test_empty_warnings_raises(self) -> None:
        with self.assertRaises(ValueError):
            pa.format_exemption_commit_message(12345, [])

    def test_single_warning(self) -> None:
        msg = pa.format_exemption_commit_message(12345, ["gcc-compat"])
        self.assertTrue(
            msg.startswith("mass-exempt -Wgcc-compat\n\nWarnings will soon")
        )
        self.assertNotIn("Warnings exempted:", msg)
        self.assertIn("Bug: 12345\n", msg)

    def test_multiple_warnings(self) -> None:
        msg = pa.format_exemption_commit_message(
            12345, ["gcc-compat", "format-security"]
        )
        self.assertTrue(msg.startswith("mass-exempt 2 warnings\n\n"))
        self.assertIn(
            "Warnings exempted:\n- -Wformat-security\n- -Wgcc-compat\n\n", msg
        )
        self.assertIn("Bug: 12345\n", msg)

    def test_deduplication_and_sorting(self) -> None:
        msg = pa.format_exemption_commit_message(
            12345, ["-Wno-foo", "foo", "-Wbar"]
        )
        self.assertTrue(msg.startswith("mass-exempt 2 warnings\n\n"))
        self.assertIn("Warnings exempted:\n- -Wbar\n- -Wfoo\n\n", msg)

    def test_for_soong_single_warning(self) -> None:
        msg = pa.format_exemption_commit_message(
            12345, ["gcc-compat"], for_soong=True
        )
        self.assertTrue(
            msg.startswith(
                "cc: set no-error for -Wgcc-compat\n\nSet -Wno-error"
            )
        )
        self.assertNotIn("Warnings exempted:", msg)
        self.assertNotIn("Flag: EXEMPT BUGFIX", msg)
        self.assertIn("See go/android-llvm-warning-suppression", msg)
        self.assertIn("Bug: 12345\n", msg)

    def test_for_soong_multiple_warnings(self) -> None:
        msg = pa.format_exemption_commit_message(
            12345, ["gcc-compat", "format-security"], for_soong=True
        )
        self.assertTrue(msg.startswith("cc: set no-error for 2 warnings\n\n"))
        self.assertIn(
            "Warnings exempted:\n- -Wformat-security\n- -Wgcc-compat\n\n", msg
        )
        self.assertNotIn("Flag: EXEMPT BUGFIX", msg)
        self.assertIn("See go/android-llvm-warning-suppression", msg)
        self.assertIn("Bug: 12345\n", msg)


class TestUpdateGlobalGoContent(unittest.TestCase):
    """Tests for update_global_go_content."""

    def test_update_global_go_content_success(self) -> None:
        initial = textwrap.dedent(
            """\
            package config

            var (
            \tnoOverrideGlobalCflags = []string{
            \t\t"-Werror=address-of-temporary",
            \t}
            )
            """
        )
        updated = pa.update_global_go_content(
            initial, 12345, ["foo", "-Wbar", "-Wno-baz"]
        )
        self.assertIn(
            "// Temporarily force no-error for these as part of suppression "
            "for b/12345",
            updated,
        )
        self.assertIn('"-Wno-error=bar",', updated)
        self.assertIn('"-Wno-error=baz",', updated)
        self.assertIn('"-Wno-error=foo",', updated)

    def test_update_global_go_content_missing_start_brace(self) -> None:
        initial = "package config\n"
        with self.assertRaisesRegex(
            ValueError, "noOverrideGlobalCflags not found in content"
        ):
            pa.update_global_go_content(initial, 12345, ["foo"])

    def test_update_global_go_content_missing_end_brace(self) -> None:
        initial = "package config\nvar noOverrideGlobalCflags = []string{\n"
        with self.assertRaisesRegex(
            ValueError, "Closing brace for noOverrideGlobalCflags not found"
        ):
            pa.update_global_go_content(initial, 12345, ["foo"])

    def test_update_global_go_content_single_warning(self) -> None:
        initial = (
            "package config\n\nvar noOverrideGlobalCflags = []string{\n}\n"
        )
        updated = pa.update_global_go_content(initial, 12345, ["foo"])
        self.assertIn(
            "// Temporarily force no-error for this as part of suppression "
            "for b/12345",
            updated,
        )
        self.assertIn('"-Wno-error=foo",', updated)
        self.assertTrue(updated.endswith("}\n"))

    def test_update_global_go_content_comment_occurrence_not_confused(
        self,
    ) -> None:
        initial = textwrap.dedent(
            """\
            package config
            // -Wno-error=foo in comment should not prevent adding to slice
            var (
            \tnoOverrideGlobalCflags = []string{
            \t\t"-Werror=address-of-temporary",
            \t}
            )
            """
        )
        updated = pa.update_global_go_content(initial, 12345, ["foo"])
        self.assertIn('"-Wno-error=foo",', updated)


class TestAddGlobalNoErrorPostflags(test_helpers.TempDirTestCase):
    """Tests for add_global_no_error_postflags."""

    def test_add_global_no_error_postflags_success(self) -> None:
        android_tree = self.make_tempdir()
        global_go_dir = android_tree / "build/soong/cc/config"
        global_go_dir.mkdir(parents=True)
        global_go_file = global_go_dir / "global.go"
        global_go_file.write_text(
            textwrap.dedent(
                """\
                package config

                var (
                \tnoOverrideGlobalCflags = []string{
                \t\t"-Werror=address-of-temporary",
                \t}
                )
                """
            ),
            encoding="utf-8",
        )

        with mock.patch.object(pa, "checked_subprocess_run"):
            result = pa.add_global_no_error_postflags(
                android_tree, 12345, ["foo", "-Wbar", "-Wno-baz"]
            )
        self.assertEqual(result, Path("build/soong/cc/config/global.go"))

        content = global_go_file.read_text(encoding="utf-8")
        self.assertIn(
            "// Temporarily force no-error for these as part of suppression "
            "for b/12345",
            content,
        )
        self.assertIn('"-Wno-error=bar",', content)
        self.assertIn('"-Wno-error=baz",', content)
        self.assertIn('"-Wno-error=foo",', content)

    def test_add_global_no_error_postflags_missing_file(self) -> None:
        android_tree = self.make_tempdir()
        with self.assertRaises(FileNotFoundError):
            pa.add_global_no_error_postflags(android_tree, 12345, ["foo"])
