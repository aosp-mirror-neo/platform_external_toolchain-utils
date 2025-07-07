#!/usr/bin/env python3
# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for pgo_utils."""

from pathlib import Path
import textwrap
import unittest
from unittest import mock

from pgo_tools import pgo_utils


class Test(unittest.TestCase):
    """Tests for pgo_utils."""

    @mock.patch.object(pgo_utils, "run")
    def test_pgo_generate_checking_works(self, mock_run):
        equery_u_output = textwrap.dedent(
            """\
            [ Legend : U - final flag setting for installation]
            [        : I - package is installed with flag     ]
            [ Colors : set, unset                             ]
             * Found these USE flags for sys-devel/llvm:
             U I
             - - llvm-next_pgo_use              : <unknown>
             - - llvm-tot                       : <unknown>
             - + llvm_pgo_generate              : <unknown>
             + - llvm_pgo_use                   : <unknown>
             """
        )
        mock_run.return_value.stdout = equery_u_output
        self.assertTrue(pgo_utils.installed_llvm_has_pgo_generate_enabled())

        mock_run.assert_called_once()

        mock_run.return_value.stdout = equery_u_output.replace(
            "+ llvm_pgo_generate", "- llvm_pgo_generate"
        )
        self.assertFalse(pgo_utils.installed_llvm_has_pgo_generate_enabled())

    @mock.patch.object(pgo_utils, "run")
    def test_pgo_generate_checking_raises_on_zero_pgo_updates(self, mock_run):
        mock_run.return_value.stdout = textwrap.dedent(
            """\
            [ Legend : U - final flag setting for installation]
             - - llvm-next_pgo_use              : <unknown>
             - - llvm-tot                       : <unknown>
             + - llvm_pgo_use                   : <unknown>
             """
        )
        with self.assertRaisesRegex(ValueError, "^No llvm_pgo_generate"):
            pgo_utils.installed_llvm_has_pgo_generate_enabled()

    @mock.patch.object(pgo_utils, "run")
    def test_pgo_generate_checking_raises_on_many_pgo_updates(self, mock_run):
        mock_run.return_value.stdout = textwrap.dedent(
            """\
            [ Legend : U - final flag setting for installation]
             - - llvm-next_pgo_use              : <unknown>
             - - llvm-tot                       : <unknown>
             - + llvm_pgo_generate              : <unknown>
             - + llvm_pgo_generate              : <unknown>
             + - llvm_pgo_use                   : <unknown>
             """
        )
        with self.assertRaisesRegex(ValueError, "^Multiple llvm_pgo_generate"):
            pgo_utils.installed_llvm_has_pgo_generate_enabled()

    @mock.patch.object(pgo_utils, "run")
    def test_pgo_generate_ignores_nonexact_use_flags(self, mock_run):
        mock_run.return_value.stdout = textwrap.dedent(
            """\
            [ Legend : U - final flag setting for installation]
             - - llvm-next_pgo_use              : <unknown>
             - - llvm-tot                       : <unknown>
             - + llvm_pgo_generate              : <unknown>
             - - llvm_pgo_generate2             : <unknown>
             - - 2llvm_pgo_generate             : <unknown>
             + - llvm_pgo_use                   : <unknown>
             """
        )
        self.assertTrue(pgo_utils.installed_llvm_has_pgo_generate_enabled())

    def test_quickpkg_restoration_works(self):
        self.assertEqual(
            pgo_utils.generate_quickpkg_restoration_command(
                Path("/path/to/sys-devel/llvm-1234-r1.tbz2")
            ),
            ["sudo", "emerge", "--usepkgonly", "=sys-devel/llvm-1234-r1"],
        )

    def test_temporary_file_creation_works(self):
        with pgo_utils.temporary_file("foo_bar_") as tmp:
            self.assertTrue(tmp.name.startswith("foo_bar_"), tmp.name)
            self.assertTrue(tmp.exists())
        self.assertFalse(tmp.exists())

    def test_temporary_file_deletion_is_fine_if_file_does_not_exist(self):
        # This test ensures this `with`'s `__exit__` block doesn't `raise`.
        with pgo_utils.temporary_file("foo_bar_") as tmp:
            tmp.unlink()
