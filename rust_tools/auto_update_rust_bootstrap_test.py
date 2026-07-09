# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for auto_update_rust_bootstrap."""

from pathlib import Path
import textwrap
from unittest import mock

from llvm_tools import test_helpers
from rust_tools import auto_update_rust_bootstrap


class Test(test_helpers.TempDirTestCase):
    """Tests for auto_update_rust_bootstrap."""

    def setUp(self) -> None:
        super().setUp()
        self.tempdir = self.make_tempdir()

    def test_ebuild_linking_logic_handles_direct_relative_symlinks(
        self,
    ) -> None:
        target = self.tempdir / "target.ebuild"
        target.touch()
        (self.tempdir / "symlink.ebuild").symlink_to(target.name)
        self.assertTrue(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_ebuild_linking_logic_handles_direct_absolute_symlinks(
        self,
    ) -> None:
        target = self.tempdir / "target.ebuild"
        target.touch()
        (self.tempdir / "symlink.ebuild").symlink_to(target)
        self.assertTrue(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_ebuild_linking_logic_handles_indirect_relative_symlinks(
        self,
    ) -> None:
        target = self.tempdir / "target.ebuild"
        target.touch()
        (self.tempdir / "symlink.ebuild").symlink_to(
            Path("..") / self.tempdir.name / target.name
        )
        self.assertTrue(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_ebuild_linking_logic_handles_broken_symlinks(self) -> None:
        target = self.tempdir / "target.ebuild"
        target.touch()
        (self.tempdir / "symlink.ebuild").symlink_to("doesnt_exist.ebuild")
        self.assertFalse(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_ebuild_linking_logic_only_steps_through_one_symlink(self) -> None:
        target = self.tempdir / "target.ebuild"
        target.symlink_to("doesnt_exist.ebuild")
        (self.tempdir / "symlink.ebuild").symlink_to(target.name)
        self.assertTrue(
            auto_update_rust_bootstrap.is_ebuild_linked_to_in_dir(target)
        )

    def test_version_has_prebuilt_detection_works_when_disabled(self) -> None:
        ebuild_contents = textwrap.dedent(
            """
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_PREBUILT_NAME=    # a comment

            # Another comment for posterity
            """
        )
        self.assertFalse(
            auto_update_rust_bootstrap.is_rust_bootstrap_using_prebuilts(
                ebuild_contents
            )
        )

    def test_version_has_prebuilt_detection_works_when_enabled(self) -> None:
        ebuild_contents = textwrap.dedent(
            """
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_PREBUILT_NAME=foo    # a comment

            # Another comment for posterity
            """
        )
        self.assertTrue(
            auto_update_rust_bootstrap.is_rust_bootstrap_using_prebuilts(
                ebuild_contents
            )
        )

    def test_version_has_prebuilt_modification_works(self) -> None:
        ebuild_contents = textwrap.dedent(
            """
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_PREBUILT_NAME=    # a comment
            # Another comment for posterity
            """
        )
        with_set_has_ebuild = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prebuilt_use(
                ebuild_contents,
                prebuilt_name="foo",
            )
        )
        self.assertIn(
            "THIS_VERSION_PREBUILT_NAME=foo    # a comment\n",
            with_set_has_ebuild,
        )

        with_unset_has_ebuild = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prebuilt_use(
                ebuild_contents,
                prebuilt_name=None,
            )
        )
        self.assertEqual(ebuild_contents, with_unset_has_ebuild)

    def test_version_has_prebuilt_modification_works_without_comment(
        self,
    ) -> None:
        ebuild_contents = textwrap.dedent(
            """
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_PREBUILT_NAME=

            # Another comment for posterity
            """
        )
        with_set_has_ebuild = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prebuilt_use(
                ebuild_contents,
                prebuilt_name="foo",
            )
        )
        self.assertIn("THIS_VERSION_PREBUILT_NAME=foo", with_set_has_ebuild)

    def test_version_has_prebuilt_unsetting_works_with_comment(self) -> None:
        ebuild_contents = textwrap.dedent(
            """
            # Some copyright
            FOO=bar
            # Comment about this cool var
            THIS_VERSION_PREBUILT_NAME=foo.tbz2 # qux

            # Another comment for posterity
            """
        )
        with_set_has_ebuild = (
            auto_update_rust_bootstrap.set_rust_bootstrap_prebuilt_use(
                ebuild_contents,
                prebuilt_name=None,
            )
        )
        self.assertIn("THIS_VERSION_PREBUILT_NAME= # qux", with_set_has_ebuild)

    def test_set_rust_bootstrap_prior_version_works(self) -> None:
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

    def test_collect_ebuilds_by_version_ignores_old_versions_and_9999(
        self,
    ) -> None:
        ebuild_170 = self.tempdir / "rust-bootstrap-1.70.0.ebuild"
        ebuild_170.touch()
        ebuild_170_r1 = self.tempdir / "rust-bootstrap-1.70.0-r1.ebuild"
        ebuild_170_r1.touch()
        ebuild_171_r2 = self.tempdir / "rust-bootstrap-1.71.1-r2.ebuild"
        ebuild_171_r2.touch()
        (self.tempdir / "rust-bootstrap-9999.ebuild").touch()

        self.assertEqual(
            auto_update_rust_bootstrap.collect_stable_ebuilds_by_version(
                self.tempdir
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

    def test_ebuild_version_parsing_works(self) -> None:
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

    def test_raw_ebuild_version_parsing_works(self) -> None:
        self.assertEqual(
            auto_update_rust_bootstrap.parse_raw_ebuild_version("1.70.0-r2"),
            auto_update_rust_bootstrap.EbuildVersion(
                major=1, minor=70, patch=0, rev=2
            ),
        )

        with self.assertRaises(ValueError):
            auto_update_rust_bootstrap.parse_ebuild_version("2.80.3_pre1234")

    def test_version_deletion_does_nothing_if_all_versions_are_needed(
        self,
    ) -> None:
        rust = self.tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = self.tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.70.0-r2.ebuild").touch()

        self.assertFalse(
            auto_update_rust_bootstrap.maybe_delete_old_rust_bootstrap_ebuilds(
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=True,
            )
        )

    def test_version_deletion_ignores_newer_than_needed_versions(self) -> None:
        rust = self.tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = self.tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.70.0-r2.ebuild").touch()
        (rust_bootstrap / "rust-bootstrap-1.71.0-r1.ebuild").touch()
        (rust_bootstrap / "rust-bootstrap-1.72.0.ebuild").touch()

        self.assertFalse(
            auto_update_rust_bootstrap.maybe_delete_old_rust_bootstrap_ebuilds(
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=True,
            )
        )

    @mock.patch.object(
        auto_update_rust_bootstrap, "update_ebuild_manifest_in_chroot"
    )
    def test_version_deletion_deletes_old_files(
        self, update_ebuild_manifest_in_chroot: mock.MagicMock
    ) -> None:
        rust = self.tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = self.tempdir / "rust-bootstrap"
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
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=False,
                commit=False,
            )
        )
        update_ebuild_manifest_in_chroot.assert_called_once()

        self.assertFalse(bootstrap_1_68_symlink.exists())
        self.assertFalse(bootstrap_1_68_symlink_abs.exists())
        self.assertFalse(bootstrap_1_69_regular.exists())
        self.assertFalse(bootstrap_1_69_symlink.exists())
        self.assertTrue(needed_rust_bootstrap.exists())

    def test_version_deletion_raises_when_old_file_has_dep(self) -> None:
        rust = self.tempdir / "rust"
        rust.mkdir()
        (rust / "rust-1.71.0-r1.ebuild").touch()
        rust_bootstrap = self.tempdir / "rust-bootstrap"
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
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=True,
            )

    def test_prebuilt_commit_message_generation_with_one_update(self) -> None:
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

    def test_prebuilt_commit_message_generation_with_multiple_updates(
        self,
    ) -> None:
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

    @mock.patch.object(
        auto_update_rust_bootstrap,
        "update_ebuild_manifest_in_chroot",
        autospec=True,
    )
    @mock.patch.object(auto_update_rust_bootstrap.gs, "ls", autospec=True)
    def test_ensure_rust_bootstrap_version_creates_ebuild(
        self,
        mock_gs_ls: mock.MagicMock,
        mock_update_manifest: mock.MagicMock,
    ) -> None:
        rust_bootstrap = self.tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        rust_bootstrap_1_95 = rust_bootstrap / "rust-bootstrap-1.95.0.ebuild"
        rust_bootstrap_1_95.write_text(
            "THIS_VERSION_PREBUILT_NAME=foo\n"
            'PRIOR_RUST_BOOTSTRAP_VERSION="1.94.0"\n',
            encoding="utf-8",
        )

        mock_gs_ls.return_value = [
            auto_update_rust_bootstrap.gs.GsEntry(
                last_modified=None,
                gs_path=(
                    "gs://chromeos-localmirror/distfiles/"
                    "rustc-1.96.0-src.tar.gz"
                ),
            )
        ]

        self.assertTrue(
            auto_update_rust_bootstrap.ensure_rust_bootstrap_version(
                target_version=auto_update_rust_bootstrap.EbuildVersion(
                    1, 96, 0, 0
                ),
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=False,
                commit=False,
            )
        )
        mock_update_manifest.assert_called_once()

        rust_bootstrap_1_96 = rust_bootstrap / "rust-bootstrap-1.96.0.ebuild"
        self.assertTrue(rust_bootstrap_1_96.exists())
        contents = rust_bootstrap_1_96.read_text(encoding="utf-8")
        self.assertIn('PRIOR_RUST_BOOTSTRAP_VERSION="1.95.0"', contents)
        self.assertIn("THIS_VERSION_PREBUILT_NAME=\n", contents)

    def test_ensure_rust_bootstrap_version_is_nop_if_already_exists(
        self,
    ) -> None:
        rust_bootstrap = self.tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.95.0.ebuild").touch()

        self.assertFalse(
            auto_update_rust_bootstrap.ensure_rust_bootstrap_version(
                target_version=auto_update_rust_bootstrap.EbuildVersion(
                    1, 95, 0, 0
                ),
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=True,
            )
        )

    @mock.patch.object(auto_update_rust_bootstrap.gs, "ls", autospec=True)
    def test_ensure_rust_bootstrap_version_handles_major_version_bump(
        self, mock_gs_ls: mock.MagicMock
    ) -> None:
        rust_bootstrap = self.tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.99.0.ebuild").write_text(
            "THIS_VERSION_PREBUILT_NAME=foo\n"
            'PRIOR_RUST_BOOTSTRAP_VERSION="1.98.0"\n',
            encoding="utf-8",
        )
        mock_gs_ls.return_value = [
            auto_update_rust_bootstrap.gs.GsEntry(
                last_modified=None,
                gs_path=(
                    "gs://chromeos-localmirror/distfiles/"
                    "rustc-2.0.0-src.tar.gz"
                ),
            )
        ]

        self.assertTrue(
            auto_update_rust_bootstrap.ensure_rust_bootstrap_version(
                target_version=auto_update_rust_bootstrap.EbuildVersion(
                    2, 0, 0, 0
                ),
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=True,
            )
        )

    def test_ensure_rust_bootstrap_version_raises_on_empty_dir(self) -> None:
        rust_bootstrap = self.tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()

        with self.assertRaises(ValueError):
            auto_update_rust_bootstrap.ensure_rust_bootstrap_version(
                target_version=auto_update_rust_bootstrap.EbuildVersion(
                    1, 96, 0, 0
                ),
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=True,
            )

    def test_ensure_rust_bootstrap_version_raises_on_discontiguous_gap(
        self,
    ) -> None:
        rust_bootstrap = self.tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.95.0.ebuild").touch()

        with self.assertRaises(ValueError):
            auto_update_rust_bootstrap.ensure_rust_bootstrap_version(
                target_version=auto_update_rust_bootstrap.EbuildVersion(
                    1, 97, 0, 0
                ),
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=True,
            )

    @mock.patch.object(auto_update_rust_bootstrap.gs, "ls", autospec=True)
    def test_ensure_rust_bootstrap_version_raises_if_source_missing(
        self, mock_gs_ls: mock.MagicMock
    ) -> None:
        rust_bootstrap = self.tempdir / "rust-bootstrap"
        rust_bootstrap.mkdir()
        (rust_bootstrap / "rust-bootstrap-1.95.0.ebuild").touch()
        mock_gs_ls.return_value = []

        with self.assertRaises(ValueError):
            auto_update_rust_bootstrap.ensure_rust_bootstrap_version(
                target_version=auto_update_rust_bootstrap.EbuildVersion(
                    1, 96, 0, 0
                ),
                chromiumos_overlay=self.tempdir,
                chromiumos_checkout=self.tempdir,
                rust_bootstrap_dir=rust_bootstrap,
                dry_run=True,
            )
