# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for generate_pgo_profile."""

from unittest import mock

from llvm_tools import test_helpers
from pgo_tools import generate_pgo_profile
from pgo_tools import pgo_utils


class Test(test_helpers.TempDirTestCase):
    """Tests for generate_pgo_profile."""

    def test_read_exactly_one_dirent_works(self) -> None:
        tempdir = self.make_tempdir()
        ent = tempdir / "one-ent"
        ent.touch()

        self.assertEqual(
            generate_pgo_profile.read_exactly_one_dirent(tempdir), ent
        )

    def test_read_exactly_one_dirent_fails_when_no_ents(self) -> None:
        tempdir = self.make_tempdir()
        with self.assertRaisesRegex(ValueError, "^Expected exactly one"):
            generate_pgo_profile.read_exactly_one_dirent(tempdir)

    def test_read_exactly_one_dirent_fails_when_multiple_ents(self) -> None:
        tempdir = self.make_tempdir()
        (tempdir / "a").touch()
        (tempdir / "b").touch()
        with self.assertRaisesRegex(ValueError, "^Expected exactly one"):
            generate_pgo_profile.read_exactly_one_dirent(tempdir)

    @mock.patch.object(pgo_utils, "run")
    def test_profraw_conversion_works(self, mock_run: mock.MagicMock) -> None:
        tempdir = self.make_tempdir()
        profiles = [
            tempdir / "profile-foo.profraw",
            tempdir / "profile-bar.profraw",
        ]
        not_a_profile = tempdir / "not-a-profile.profraw"
        for f in profiles + [not_a_profile]:
            f.touch()

        result = generate_pgo_profile.convert_profraw_to_pgo_profile(tempdir)
        self.assertNotEqual(result.stem, ".profraw")
        try:
            # is_relative_to was added in Py3.9; until the chroot has that,
            # this code needs to use `relative_to` & check for exceptions.
            result.relative_to(tempdir)
        except ValueError:
            self.fail(f"{result} should be relative to {tempdir}")

        mock_run.assert_called_once()
        run_cmd = mock_run.call_args[0][0]
        for p in profiles:
            self.assertIn(p, run_cmd)
        self.assertNotIn(not_a_profile, run_cmd)
        self.assertIn(f"--output={result}", run_cmd)
