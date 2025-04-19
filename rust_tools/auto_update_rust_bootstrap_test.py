# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for auto_update_rust_bootstrap."""

from pathlib import Path
import shutil
import tempfile
import textwrap
import unittest
from unittest import mock

from rust_tools import auto_update_rust_bootstrap


class Test(unittest.TestCase):
    """Tests for auto_update_rust_bootstrap."""

    def make_tempdir(self) -> Path:
        tempdir = Path(
            tempfile.mkdtemp(prefix="auto_update_rust_bootstrap_test_")
        )
        self.addCleanup(shutil.rmtree, tempdir)
        return tempdir

    def test_ebuild_linking_logic_handles_direct_relative_symlinks(self):
        tempdir = self.make_tempdir()
        target = tempdir / "target.ebuild"
        target.touch()
        (tempdir / "symlink.ebuild").symlink_to(target.name)
        self.assertTrue(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_ebuild_linking_logic_handles_direct_absolute_symlinks(self):
        tempdir = self.make_tempdir()
        target = tempdir / "target.ebuild"
        target.touch()
        (tempdir / "symlink.ebuild").symlink_to(target)
        self.assertTrue(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_ebuild_linking_logic_handles_indirect_relative_symlinks(self):
        tempdir = self.make_tempdir()
        target = tempdir / "target.ebuild"
        target.touch()
        (tempdir / "symlink.ebuild").symlink_to(
            Path("..") / tempdir.name / target.name
        )
        self.assertTrue(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_ebuild_linking_logic_handles_broken_symlinks(self):
        tempdir = self.make_tempdir()
        target = tempdir / "target.ebuild"
        target.touch()
        (tempdir / "symlink.ebuild").symlink_to("doesnt_exist.ebuild")
        self.assertFalse(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_ebuild_linking_logic_only_steps_through_one_symlink(self):
        tempdir = self.make_tempdir()
        target = tempdir / "target.ebuild"
        target.symlink_to("doesnt_exist.ebuild")
        (tempdir / "symlink.ebuild").symlink_to(target.name)
        self.assertTrue(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_version_has_prebuilt_detection_works(self):
        ebuild_contents = textwrap.dedent(
            """\
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_HAS_PREBUILT=     # a comment

            # Another comment for posterity
            """
        )
        self.assertFalse(
            auto_update_rust_bootstrap.is_rust_bootstrap_using_prebuilts(
                ebuild_contents
            )
        )

    def test_version_has_prebuilt_modification_works(self):
        ebuild_contents = textwrap.dedent(
            """\
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_HAS_PREBUILT=     # a comment
            # Another comment for posterity
            """
        )
        with_set_has_ebuild = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prebuilt_use(
                ebuild_contents,
                use_prebuilts=True,
            )
        )
        self.assertIn(
            "THIS_VERSION_HAS_PREBUILT=1     # a comment\n", with_set_has_ebuild
        )

        with_unset_has_ebuild = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prebuilt_use(
                ebuild_contents,
                use_prebuilts=False,
            )
        )
        self.assertEqual(ebuild_contents, with_unset_has_ebuild)

    def test_version_has_prebuilt_modification_works_without_comment(self):
        ebuild_contents = textwrap.dedent(
            """\
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_HAS_PREBUILT=

            # Another comment for posterity
            """
        )
        with_set_has_ebuild = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prebuilt_use(
                ebuild_contents,
                use_prebuilts=True,
            )
        )
        self.assertIn("THIS_VERSION_HAS_PREBUILT=1", with_set_has_ebuild)

    def test_version_has_prebuilt_unsetting_works_with_comment(self):
        ebuild_contents = textwrap.dedent(
            """\
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_HAS_PREBUILT=" 1" # baz

            # Another comment for posterity
            """
        )
        with_set_has_ebuild = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prebuilt_use(
                ebuild_contents,
                use_prebuilts=False,
            )
        )
        self.assertIn("THIS_VERSION_HAS_PREBUILT= # baz", with_set_has_ebuild)

    def test_set_rust_bootstrap_prior_version_works(self):
        ebuild_contents = textwrap.dedent(
            """\
            # Some copyright
            FOO=bar
            # Comment about this cool var
            PRIOR_RUST_BOOTSTRAP_VERSION="foo"

            # Another comment for posterity
            """
        )
        with_update = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prior_version(
                ebuild_contents,
                new_version=auto_update_rust_bootstrap.EbuildVersion(
                    major=1,
                    minor=2,
                    patch=3,
                    rev=4,
                ),
            )
        )
        self.assertIn('PRIOR_RUST_BOOTSTRAP_VERSION="1.2.3"', with_update)

    def test_collect_ebuilds_by_version_ignores_old_versions_and_9999(self):
        tempdir = self.make_tempdir()
        ebuild_170 = tempdir / "rust-bootstrap-1.70.0.ebuild"
        ebuild_170.touch()
        ebuild_170_r1 = tempdir / "rust-bootstrap-1.70.0-r1.ebuild"
        ebuild_170_r1.touch()
        ebuild_171_r2 = tempdir / "rust-bootstrap-1.71.1-r2.ebuild"
        ebuild_171_r2.touch()
        (tempdir / "rust-bootstrap-9999.ebuild").touch()

        self.assertEqual(
            auto_update_rust_bootstrap.collect_stable_ebuilds_by_version(
                tempdir
            ),
            [
                (
                    auto_update_rust_bootstrap.EbuildVersion(
                        major=1, minor=70, patch=0, rev=1
                    ),
                    ebuild_170_r1,
                ),
                (
                    auto_update_rust_bootstrap.EbuildVersion(
                        major=1, minor=71, patch=1, rev=2
                    ),
                    ebuild_171_r2,
                ),
            ],
        )

    def test_ebuild_version_parsing_works(self):
        self.assertEqual(
            auto_update_rust_bootstrap.parse_ebuild_version(
                "rust-bootstrap-1.70.0-r2.ebuild"
            ),
            auto_update_rust_bootstrap.EbuildVersion(
                major=1, minor=70, patch=0, rev=2
            ),
        )

        self.assertEqual(
            auto_update_rust_bootstrap.parse_ebuild_version(
                "rust-bootstrap-2.80.3.ebuild"
            ),
            auto_update_rust_bootstrap.EbuildVersion(
                major=2, minor=80, patch=3, rev=0
            ),
        )

        with self.assertRaises(ValueError):
            auto_update_rust_bootstrap.parse_ebuild_version(
                "rust-bootstrap-2.80.3_pre1234.ebuild"
            )

    def test_raw_ebuild_version_parsing_works(self):
        self.assertEqual(
            auto_update_rust_bootstrap.parse_raw_ebuild_version("1.70.0-r2"),
            auto_update_rust_bootstrap.EbuildVersion(
                major=1, minor=70, patch=0, rev=2
            ),
        )

        with self.assertRaises(ValueError):
            auto_update_rust_bootstrap.parse_ebuild_version("2.80.3_pre1234")

    def test_ensure_newest_version_does_nothing_if_no_new_rust_version(self):
        tempdir = self.make_tempdir()
        rust = tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.70.0-r1.ebuild").touch()
        rust_bootstrap = tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.70.0.ebuild").touch()

        self.assertFalse(
            auto_update_rust_bootstrap.maybe_add_new_rust_bootstrap_version(
                tempdir, rust_bootstrap, dry_run=True
            )
        )

    @mock.patch.object(auto_update_rust_bootstrap, "update_ebuild_manifest")
    def test_ensure_newest_version_upgrades_rust_bootstrap_properly(
        self, update_ebuild_manifest
    ):
        tempdir = self.make_tempdir()
        rust = tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        rust_bootstrap_1_70 = rust_bootstrap / "rust-bootstrap-1.70.0-r2.ebuild"

        rust_bootstrap_contents = textwrap.dedent(
            """\
            # Some copyright
            FOO=bar

            THIS_VERSION_HAS_PREBUILT=1
            PRIOR_RUST_BOOTSTRAP_VERSION="1.69.0"
            """
        )
        rust_bootstrap_1_70.write_text(
            rust_bootstrap_contents, encoding="utf-8"
        )

        self.assertTrue(
            auto_update_rust_bootstrap.maybe_add_new_rust_bootstrap_version(
                tempdir, rust_bootstrap, dry_run=False, commit=False
            )
        )
        update_ebuild_manifest.assert_called_once()
        rust_bootstrap_1_71 = rust_bootstrap / "rust-bootstrap-1.71.0.ebuild"

        self.assertTrue(
            rust_bootstrap_1_70.read_text(encoding="utf-8"),
            rust_bootstrap_contents,
        )
        new_contents = rust_bootstrap_1_71.read_text(encoding="utf-8")
        self.assertIn(
            "THIS_VERSION_HAS_PREBUILT=\n",
            new_contents,
        )
        self.assertIn(
            'PRIOR_RUST_BOOTSTRAP_VERSION="1.70.0"\n',
            new_contents,
        )

    def test_version_deletion_does_nothing_if_all_versions_are_needed(self):
        tempdir = self.make_tempdir()
        rust = tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.70.0-r2.ebuild").touch()

        self.assertFalse(
            auto_update_rust_bootstrap.maybe_delete_old_rust_bootstrap_ebuilds(
                tempdir, rust_bootstrap, dry_run=True
            )
        )

    def test_version_deletion_ignores_newer_than_needed_versions(self):
        tempdir = self.make_tempdir()
        rust = tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.70.0-r2.ebuild").touch()
        (rust_bootstrap / "rust-bootstrap-1.71.0-r1.ebuild").touch()
        (rust_bootstrap / "rust-bootstrap-1.72.0.ebuild").touch()

        self.assertFalse(
            auto_update_rust_bootstrap.maybe_delete_old_rust_bootstrap_ebuilds(
                tempdir, rust_bootstrap, dry_run=True
            )
        )

    @mock.patch.object(auto_update_rust_bootstrap, "update_ebuild_manifest")
    def test_version_deletion_deletes_old_files(self, update_ebuild_manifest):
        tempdir = self.make_tempdir()
        rust = tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        needed_rust_bootstrap = (
            rust_bootstrap / "rust-bootstrap-1.70.0-r2.ebuild"
        )
        needed_rust_bootstrap.touch()

        # There are quite a few of these, so corner-cases are tested.

        # Symlink to outside of the group of files to delete.
        bootstrap_1_68_symlink = rust_bootstrap / "rust-bootstrap-1.68.0.ebuild"
        bootstrap_1_68_symlink.symlink_to(needed_rust_bootstrap.name)
        # Ensure that absolute symlinks are caught.
        bootstrap_1_68_symlink_abs = (
            rust_bootstrap / "rust-bootstrap-1.68.0-r1.ebuild"
        )
        bootstrap_1_68_symlink_abs.symlink_to(needed_rust_bootstrap)
        # Regular files should be no issue.
        bootstrap_1_69_regular = rust_bootstrap / "rust-bootstrap-1.69.0.ebuild"
        bootstrap_1_69_regular.touch()
        # Symlinks linking back into the set of files to delete should also be
        # no issue.
        bootstrap_1_69_symlink = (
            rust_bootstrap / "rust-bootstrap-1.69.0-r2.ebuild"
        )
        bootstrap_1_69_symlink.symlink_to(bootstrap_1_69_regular.name)

        self.assertTrue(
            auto_update_rust_bootstrap.maybe_delete_old_rust_bootstrap_ebuilds(
                tempdir,
                rust_bootstrap,
                dry_run=False,
                commit=False,
            )
        )
        update_ebuild_manifest.assert_called_once()

        self.assertFalse(bootstrap_1_68_symlink.exists())
        self.assertFalse(bootstrap_1_68_symlink_abs.exists())
        self.assertFalse(bootstrap_1_69_regular.exists())
        self.assertFalse(bootstrap_1_69_symlink.exists())
        self.assertTrue(needed_rust_bootstrap.exists())

    def test_version_deletion_raises_when_old_file_has_dep(self):
        tempdir = self.make_tempdir()
        rust = tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        old_rust_bootstrap = rust_bootstrap / "rust-bootstrap-1.69.0-r1.ebuild"
        old_rust_bootstrap.touch()
        (rust_bootstrap / "rust-bootstrap-1.70.0-r2.ebuild").symlink_to(
            old_rust_bootstrap.name
        )

        with self.assertRaises(
            auto_update_rust_bootstrap.OldEbuildIsLinkedToError
        ):
            auto_update_rust_bootstrap.maybe_delete_old_rust_bootstrap_ebuilds(
                tempdir, rust_bootstrap, dry_run=True
            )

    def test_prebuilt_commit_message_generation_with_one_update(self):
        msg = auto_update_rust_bootstrap.build_commit_message_for_new_prebuilts(
            [
                (
                    auto_update_rust_bootstrap.EbuildVersion(1, 70, 0, 0),
                    "gs://some/path",
                )
            ]
        )
        self.assertEqual(
            msg,
            textwrap.dedent(
                f"""\
            rust-bootstrap: use prebuilts

            This CL used the following rust-bootstrap artifact:
            - rust-bootstrap-1.70.0 => gs://some/path

            BUG={auto_update_rust_bootstrap.TRACKING_BUG}
            TEST=CQ"""
            ),
        )

    def test_prebuilt_commit_message_generation_with_multiple_updates(self):
        msg = auto_update_rust_bootstrap.build_commit_message_for_new_prebuilts(
            [
                (
                    auto_update_rust_bootstrap.EbuildVersion(1, 70, 0, 0),
                    "gs://some/path",
                ),
                (auto_update_rust_bootstrap.EbuildVersion(1, 71, 1, 0), None),
            ]
        )
        self.assertEqual(
            msg,
            textwrap.dedent(
                f"""\
            rust-bootstrap: use prebuilts

            This CL used the following rust-bootstrap artifacts:
            - rust-bootstrap-1.70.0 => gs://some/path
            - rust-bootstrap-1.71.1 was already on localmirror

            BUG={auto_update_rust_bootstrap.TRACKING_BUG}
            TEST=CQ"""
            ),
        )
