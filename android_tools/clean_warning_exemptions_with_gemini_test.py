# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for clean_warning_exemptions_with_gemini."""

import textwrap

from android_tools import clean_warning_exemptions_with_gemini as clean_warnings
from llvm_tools import test_helpers


class TestRemoveBlankLinesFromDiff(test_helpers.TempDirTestCase):
    """Tests for remove_blank_lines_from_diff."""

    def test_remove_blank_lines_from_diff_basic_example(self) -> None:
        diff = textwrap.dedent(
            """
            diff --git a/... b/...
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
            diff --git a/... b/...
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

    def test_no_blank_lines_no_change(self) -> None:
        diff = textwrap.dedent(
            """
            diff --git a/... b/...
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

    def test_multiple_hunks_with_indent(self) -> None:
        diff = textwrap.dedent(
            """
            diff --git a/... b/...
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
            diff --git a/... b/...
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

    def test_removed_and_context_blank_line_is_kept(self) -> None:
        diff = textwrap.dedent(
            """
            diff --git a/... b/...
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

    def test_remove_blank_lines_from_diff_omitted_len(self) -> None:
        """Test remove_blank_lines_from_diff with omitted hunk lengths."""
        diff = textwrap.dedent(
            """
            diff --git a/... b/...
            --- a/foo
            +++ b/foo
            @@ -1 +1 @@
            -a
            +
            """
        )
        expected_diff = textwrap.dedent(
            """
            diff --git a/... b/...
            --- a/foo
            +++ b/foo
            @@ -1,1 +1,0 @@
            -a
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff), expected_diff
        )

    def test_hunk_removal_if_only_whitespace(self) -> None:
        """Test remove_blank_lines_from_diff with omitted hunk lengths."""
        diff = textwrap.dedent(
            """
            diff --git a/... b/...
            --- a/foo
            +++ b/foo
            @@ -1,0 +1 @@
            +
            @@ -10,0 +10 @@
            +foo
            """
        )
        # While hunks with no diff are errors, files with no hunks are not.
        # Realistically, the result of blank line removal will only ever be seen
        # by git (& humans when debugging), so live with this ugliness until
        # there's a reason not to.
        expected_diff = textwrap.dedent(
            """
            diff --git a/... b/...
            --- a/foo
            +++ b/foo
            @@ -10,0 +10 @@
            +foo
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff).rstrip(),
            expected_diff.rstrip(),
        )

    def test_hunk_removal_keeps_header_if_no_hunk(self) -> None:
        """If no hunk is found, headers should be kept."""
        diff = textwrap.dedent(
            """\
            diff --git a/foo b/foo
            --- a/foo
            +++ b/foo
            diff --git a/bar b/bar
            --- a/bar
            +++ b/bar
            """
        )
        expected_diff = textwrap.dedent(
            """\
            diff --git a/foo b/foo
            --- a/foo
            +++ b/foo
            diff --git a/bar b/bar
            --- a/bar
            +++ b/bar
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff).strip(),
            expected_diff.strip(),
        )

    def test_hunk_removal_keeps_header_multiple_hunks(self) -> None:
        """If all hunks are removed, the header should be kept."""
        diff = textwrap.dedent(
            """\
            diff --git a/foo b/foo
            --- a/foo
            +++ b/foo
            @@ -0,0 +1 @@
            +
            @@ -10,0 +11 @@
            +a
            """
        )
        expected_diff = textwrap.dedent(
            """\
            diff --git a/foo b/foo
            --- a/foo
            +++ b/foo
            @@ -10,0 +11 @@
            +a
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff).strip(),
            expected_diff.strip(),
        )

    def test_multifile_hunk_removal_if_only_whitespace(self) -> None:
        """Test remove_blank_lines_from_diff with omitted hunk lengths."""
        diff = textwrap.dedent(
            """
            diff --git a/... b/...
            --- a/foo
            +++ b/foo
            @@ -1,0 +1 @@
            +
            diff --git a/... b/...
            --- a/bar
            +++ b/bar
            @@ -1,0 +1 @@
            +
            """
        )
        self.assertEqual(
            clean_warnings.remove_blank_lines_from_diff(diff).strip(),
            "",
        )


class TestDiffTriviallyHasNoDedupePotential(test_helpers.TempDirTestCase):
    """Tests for diff_trivially_has_no_dedupe_potential."""

    def test_no_hunk(self) -> None:
        diff = textwrap.dedent(
            """
            --- a/some/file.bp
            +++ b/some/file.bp
            """
        )
        self.assertTrue(
            clean_warnings.diff_trivially_has_no_dedupe_potential(diff)
        )

    def test_one_wno_flag(self) -> None:
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

    def test_multiple_different_wno_flags(self) -> None:
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

    def test_duplicate_wno_flags_same_hunk(self) -> None:
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

    def test_duplicate_wno_flags_across_hunks(self) -> None:
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

    def test_no_added_lines(self) -> None:
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

    def test_empty_diff(self) -> None:
        diff = ""
        pieces = list(clean_warnings.iterate_diff_pieces(diff))
        self.assertEqual(pieces, [""])

    def test_docstring_example(self) -> None:
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


class TestDiffHunkParsing(test_helpers.TempDirTestCase):
    """Tests for diff hunk parsing."""

    def test_no_newline_at_end_of_file_old(self) -> None:
        diff = textwrap.dedent(
            r"""
            @@ -110,4 +116,4 @@ cc_fuzz {
                    ],
                    foo: "bar",
                },
            -}
            \ No newline at end of file
            +}
            """
        )
        # Just ensure this doesn't throw; it'll do so if it can't consume
        # exactly the number of lines specified by the hunk header.
        clean_warnings.DiffHunk.parse(diff.lstrip().split("\n"))

    def test_no_newline_at_end_of_file_new(self) -> None:
        diff = textwrap.dedent(
            r"""
            @@ -110,4 +116,4 @@ cc_fuzz {
                    ],
                    foo: "bar",
                },
            -}
            +}
            \ No newline at end of file
            """
        )
        # Just ensure this doesn't throw; same reason as above.
        clean_warnings.DiffHunk.parse(diff.lstrip().split("\n"))
