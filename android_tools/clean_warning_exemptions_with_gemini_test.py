# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for clean_warning_exemptions_with_gemini."""

import textwrap

from android_tools import clean_warning_exemptions_with_gemini as clean_warnings
from llvm_tools import test_helpers


class TestRemoveBlankLinesFromDiff(test_helpers.TempDirTestCase):
    """Tests for remove_blank_lines_from_diff."""

    def test_remove_blank_lines_from_diff_basic_example(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,3 +1,5 @@
             cc_defaults {
               name: "foo",
            +  cflags: ["-Wbar"],
            +
             }
            """
        )
        expected_diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,3 +1,4 @@
             cc_defaults {
               name: "foo",
            +  cflags: ["-Wbar"],
             }
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff), expected_diff
        )

    def test_no_blank_lines_no_change(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,3 +1,4 @@
             cc_defaults {
               name: "foo",
            +  cflags: ["-Wbar"],
             }
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff), diff
        )

    def test_multiple_hunks_with_indent(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,3 +1,5 @@
             cc_defaults {
               name: "foo",
            +  cflags: ["-Wbar"],
            +
             }
            @@ -10,3 +10,6 @@
             cc_defaults {
               name: "bar",
            +
            +\t \t
            +  cflags: ["-Wbaz"],
             }
            """
        )
        expected_diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,3 +1,4 @@
             cc_defaults {
               name: "foo",
            +  cflags: ["-Wbar"],
             }
            @@ -10,3 +10,4 @@
             cc_defaults {
               name: "bar",
            +  cflags: ["-Wbaz"],
             }
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff), expected_diff
        )

    def test_removed_and_context_blank_line_is_kept(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,5 +1,4 @@
             cc_defaults {
               name: "foo",

            -
               cflags: ["-Wbar"],
             }
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff), diff
        )

    def test_remove_blank_lines_from_diff_omitted_len(self):
        """Test remove_blank_lines_from_diff with omitted hunk lengths."""
        diff = textwrap.dedent(
            """
            --- a/foo
            +++ b/foo
            @@ -1 +1 @@
            -a
            +
            """
        )
        expected_diff = textwrap.dedent(
            """
            --- a/foo
            +++ b/foo
            @@ -1,1 +1,0 @@
            -a
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff), expected_diff
        )

    def test_hunk_removal_if_only_whitespace(self):
        """Test remove_blank_lines_from_diff with omitted hunk lengths."""
        diff = textwrap.dedent(
            """
            --- a/foo
            +++ b/foo
            @@ -1,0 +1 @@
            +
            """
        )
        # While hunks with no diff are errors, files with no hunks are not.
        # Realistically, the result of blank line removal will only ever be seen
        # by git (& humans when debugging), so live with this ugliness until
        # there's a reason not to.
        expected_diff = textwrap.dedent(
            """
            --- a/foo
            +++ b/foo
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff).rstrip(),
            expected_diff.rstrip(),
        )

    def test_multifile_hunk_removal_if_only_whitespace(self):
        """Test remove_blank_lines_from_diff with omitted hunk lengths."""
        diff = textwrap.dedent(
            """
            --- a/foo
            +++ b/foo
            @@ -1,0 +1 @@
            +
            --- a/bar
            +++ b/bar
            @@ -1,0 +1 @@
            +
            """
        )
        expected_diff = textwrap.dedent(
            """
            --- a/foo
            +++ b/foo
            --- a/bar
            +++ b/bar
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff).rstrip(),
            expected_diff.rstrip(),
        )


class TestDiffTriviallyHasNoDedupePotential(test_helpers.TempDirTestCase):
    """Tests for diff_trivially_has_no_dedupe_potential."""

    def test_no_hunk(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            """
        )
        self.assertTrue(
            clean_warnings.diff_trivially_has_no_dedupe_potential(diff)
        )

    def test_one_wno_flag(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,3 +1,4 @@
             cc_library {
               name: "foo",
            +  cflags: ["-Wno-bar"],
             }
            """
        )
        self.assertTrue(
            clean_warnings.diff_trivially_has_no_dedupe_potential(diff)
        )

    def test_multiple_different_wno_flags(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,6 +1,8 @@
             cc_library {
               name: "foo",
            +  cflags: ["-Wno-bar"],
             }
             cc_library {
               name: "foo",
            +  cflags: ["-Wno-baz"],
             }
            """
        )
        self.assertTrue(
            clean_warnings.diff_trivially_has_no_dedupe_potential(diff)
        )

    def test_duplicate_wno_flags_same_hunk(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,6 +1,8 @@
             cc_library {
               name: "foo",
            +  cflags: ["-Wno-bar"],
             }
             cc_library {
               name: "foo",
            +  cflags: ["-Wno-bar"],
             }
            """
        )
        self.assertFalse(
            clean_warnings.diff_trivially_has_no_dedupe_potential(diff)
        )

    def test_duplicate_wno_flags_across_hunks(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,3 +1,4 @@
             cc_library {
               name: "foo",
            +  cflags: ["-Wno-bar"],
             }
            @@ -10,3 +11,4 @@
             cc_library {
               name: "bar",
            +  cflags: ["-Wno-bar"],
             }
            """
        )
        self.assertFalse(
            clean_warnings.diff_trivially_has_no_dedupe_potential(diff)
        )

    def test_no_added_lines(self):
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            @@ -1,4 +1,3 @@
             cc_library {
               name: "foo",
            -  cflags: ["-Wno-bar"],
             }
            """
        )
        self.assertTrue(
            clean_warnings.diff_trivially_has_no_dedupe_potential(diff)
        )


class TestIterateDiffPieces(test_helpers.TempDirTestCase):
    """Tests for iterate_diff_pieces."""

    def test_empty_diff(self):
        diff = ""
        pieces = list(clean_warnings.iterate_diff_pieces(diff))
        self.assertEqual(pieces, [""])

    def test_docstring_example(self):
        diff = (
            textwrap.dedent(
                """\
                Foo bar
                --- a/foo
                +++ b/foo
                @@ -1 +1 @@
                   line
                trailing line

                """
            ).rstrip()
            + "\n"
        )

        pieces = list(clean_warnings.iterate_diff_pieces(diff))
        expected_pieces = [
            "Foo bar",
            "--- a/foo",
            "+++ b/foo",
            clean_warnings.DiffHunk(
                header="@@ -1 +1 @@",
                lines=["   line"],
                old_start=1,
                old_len=1,
                new_start=1,
                new_len=1,
                rest="",
            ),
            "trailing line",
            "",
        ]
        self.assertEqual(pieces, expected_pieces)
