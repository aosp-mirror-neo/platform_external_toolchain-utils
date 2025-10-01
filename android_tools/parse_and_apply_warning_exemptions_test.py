# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for parse_and_apply_warning_exemptions."""

from android_tools import parse_and_apply_warning_exemptions
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

    def test_infer_target_from_cmdline_success(self):
        cmd = [
            "clang",
            "-o",
            EXAMPLE_OUT_FILE,
            "bionic/libc/bionic/sigprocmask.c",
        ]
        self.assertEqual(
            parse_and_apply_warning_exemptions.infer_target_from_cmdline(cmd),
            "//bionic/libc:libc_bionic",
        )

    def test_infer_target_from_cmdline_no_dash_o(self):
        cmd = [
            "clang",
            "bionic/libc/bionic/sigprocmask.c",
        ]
        self.assertIsNone(
            parse_and_apply_warning_exemptions.infer_target_from_cmdline(cmd)
        )

    def test_infer_target_from_cmdline_no_soong_prefix(self):
        cmd = [
            "clang",
            "-o",
            EXAMPLE_OUT_FILE.replace("/soong/", "/not-soong/", 1),
            "bionic/libc/bionic/sigprocmask.c",
        ]
        self.assertIsNone(
            parse_and_apply_warning_exemptions.infer_target_from_cmdline(cmd)
        )

    def test_infer_target_from_cmdline_no_obj_dir(self):
        cmd = [
            "clang",
            "-o",
            EXAMPLE_OUT_FILE.replace("/obj/", "/", 1),
            "bionic/libc/bionic/sigprocmask.c",
        ]
        self.assertIsNone(
            parse_and_apply_warning_exemptions.infer_target_from_cmdline(cmd)
        )


class TestParseOneWarningReport(test_helpers.TempDirTestCase):
    """Tests for parse_one_warning_report."""

    def test_parse_one_warning_report_success(self):
        target, warnings = (
            parse_and_apply_warning_exemptions.parse_one_warning_report(
                EXAMPLE_WARNING_REPORT, report_line_number=1
            )
        )
        self.assertEqual(target, "//bionic/libc:libc_bionic")
        self.assertEqual(warnings, example_warning_report_expected_result())


class TestParseWarningReports(test_helpers.TempDirTestCase):
    """Tests for parse_warning_reports."""

    def test_parse_warning_reports_success(self):
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

        result = parse_and_apply_warning_exemptions.parse_warning_reports(
            log_path
        )

        parsed_result = example_warning_report_expected_result()
        self.assertEqual(
            result,
            {"//bionic/libc:libc_bionic": parsed_result},
        )
