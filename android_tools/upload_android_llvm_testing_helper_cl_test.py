# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for upload_android_llvm_testing_helper_cl"""

import textwrap

from android_tools import upload_android_llvm_testing_helper_cl as upload_cl
from llvm_tools import test_helpers


class Test(test_helpers.TempDirTestCase):
    """Tests for upload_android_llvm_testing_helper_cl"""

    def test_add_flag_after_optimization_level_happy_case(self):
        file_contents = textwrap.dedent(
            """\
            cflags := []string{
              "-O2",
              "some other flag",
            }
            """
        )
        new_contents = upload_cl.add_flag_after_optimization_level(
            file_contents, "-my-flag"
        )
        self.assertEqual(
            new_contents,
            textwrap.dedent(
                """\
                cflags := []string{
                  "-O2",
                  "-my-flag",
                  "some other flag",
                }
                """
            ),
        )

    def test_add_flag_after_optimization_level_multiple_newlines(self):
        # The first pass at this script used a `re.MULTILINE` regex on the
        # entire `file_contents`, which produced undesirable behavior.
        # Specifically, it started with `r'^(\s+)'`. The first capture group
        # would sometimes contained a newline.
        #
        # This test-case provides a case where `re.MULTILINE`'s 'leading indent'
        # capture-group contained a newline, and ensures that the script doesn't
        # emit it before `-my-flag`.
        file_contents = textwrap.dedent(
            """\
            cflags := []string{

              "-O2",
              "some other flag",
            }
            """
        )
        new_contents = upload_cl.add_flag_after_optimization_level(
            file_contents, "-my-flag"
        )
        self.assertEqual(
            new_contents,
            textwrap.dedent(
                """\
                cflags := []string{

                  "-O2",
                  "-my-flag",
                  "some other flag",
                }
                """
            ),
        )

    def test_add_flag_after_optimization_level_bad_flag(self):
        with self.assertRaisesRegex(ValueError, "requiring escaping"):
            upload_cl.add_flag_after_optimization_level(
                "",
                '"',
            )
        with self.assertRaisesRegex(ValueError, "requiring escaping"):
            upload_cl.add_flag_after_optimization_level(
                "",
                "\\",
            )

    def test_add_flag_after_optimization_level_no_match(self):
        with self.assertRaisesRegex(
            ValueError, "Wanted exactly one match.*found 0"
        ):
            upload_cl.add_flag_after_optimization_level("foo", "-flag")

    def test_add_flag_after_optimization_level_many_matches(self):
        contents = textwrap.dedent(
            """
            cflags := []string{
              "-O2",
              "-O3",
            }
            """
        )
        with self.assertRaisesRegex(
            ValueError, "Wanted exactly one match.*found 2"
        ):
            upload_cl.add_flag_after_optimization_level(contents, "-flag")
