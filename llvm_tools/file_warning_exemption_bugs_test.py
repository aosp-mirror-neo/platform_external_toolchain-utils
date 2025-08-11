# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for file_warning_exemption_bugs."""

import dataclasses
import json
from pathlib import Path
import subprocess
import textwrap
from typing import Iterable
import unittest
from unittest import mock

# Short name so we don't have tons of unnecessarily long lines.
from llvm_tools import file_warning_exemption_bugs as fweb
from llvm_tools import test_helpers
from llvm_tools import warning_exemption


class Test(test_helpers.TempDirTestCase):
    """Tests for file_warning_exemption_bugs."""

    def test_pluralize_works(self):
        self.assertEqual(
            fweb.pluralize(3, "There are %(num)d bug%(plural)s in this code"),
            "There are 3 bugs in this code",
        )

        self.assertEqual(
            fweb.pluralize(1, "There is %(num)d bug%(plural)s in this code"),
            "There is 1 bug in this code",
        )

    @mock.patch.object(subprocess, "run")
    def test_dir_metadata_scraping_works(self, mock_run):
        mock_run.return_value = mock.MagicMock()
        mock_run.return_value.stdout = json.dumps(
            {
                "stdin": {
                    "json": {
                        "buganizerPublic": {
                            "componentId": "123",
                        }
                    }
                }
            }
        )

        self.assertEqual(fweb.scrape_component_from_dir_metadata_file(""), 123)

    @mock.patch.object(subprocess, "run")
    def test_dir_metadata_scraping_prefers_internal_component(self, mock_run):
        mock_run.return_value = mock.MagicMock()
        mock_run.return_value.stdout = json.dumps(
            {
                "stdin": {
                    "json": {
                        "buganizer": {
                            "componentId": "321",
                        },
                        "buganizerPublic": {
                            "componentId": "123",
                        },
                    }
                }
            }
        )

        self.assertEqual(fweb.scrape_component_from_dir_metadata_file(""), 321)

    @mock.patch.object(subprocess, "run")
    def test_dir_metadata_scraping_handles_no_component(self, mock_run):
        mock_run.return_value = mock.MagicMock()
        mock_run.return_value.stdout = json.dumps({"stdin": {"json": {}}})

        self.assertIsNone(fweb.scrape_component_from_dir_metadata_file(""))

    def test_format_bug_golden_case(self):
        b = fweb.format_bug(
            title="[title]",
            body="[body]",
            component=123,
            assignee="[assignee]",
            parent=321,
            priority=1,
        )
        expected_body = textwrap.dedent(
            """\
            [title]

            [body]

            COMPONENT=123
            TYPE=INTERNAL_CLEANUP
            PRIORITY=P1
            SEVERITY=S2
            ASSIGNEE=[assignee]
            PARENT+=321
            """
        )
        self.assertEqual(b, expected_body)

    @mock.patch.object(subprocess, "run")
    def test_repo_list_works(self, mock_run):
        mock_run.return_value = mock.MagicMock()
        mock_run.return_value.stdout = textwrap.dedent(
            """\
            chromite : chromiumos/chromite
            src/foo : chromiumos/foo
            src-internal/subfoo : chromiumos/foo/subfoo
            """
        )

        tempdir = Path("/dir/does/not/exist")
        repo_list = fweb.RepoList.new_from_repo(tempdir)
        self.assertEqual(
            repo_list.lookup_local_path("chromiumos/chromite/foo/bar.txt"),
            tempdir / "chromite/foo/bar.txt",
        )
        self.assertEqual(
            repo_list.lookup_local_path("chromiumos/foo/foo2/bar.md"),
            tempdir / "src/foo/foo2/bar.md",
        )
        self.assertEqual(
            repo_list.lookup_local_path("chromiumos/foo/subfoo/bar.md"),
            tempdir / "src-internal/subfoo/bar.md",
        )
        self.assertIsNone(
            repo_list.lookup_local_path("not-chromiumos/foo/bar/baz.txt")
        )

    def test_mage_followup_bug_files_nothing_if_all_warnings_addressed(self):
        exemptions = [
            warning_exemption.YamlPackageWarnings(
                package=warning_exemption.Package("foo", "bar"),
                warning_lines=[],
                warning_names=["foo"],
                observed_on=[],
            ),
        ]
        self.assertIsNone(
            fweb.format_bug_for_mage_followup(
                parent_bug=1,
                crostc_contact="foo",
                per_package_warnings=exemptions,
                frozen_per_package_warnings=exemptions,
            )
        )

        self.assertIsNone(
            fweb.format_bug_for_mage_followup(
                parent_bug=1,
                crostc_contact="foo",
                per_package_warnings=exemptions,
                frozen_per_package_warnings=[],
            )
        )

    def test_mage_followup_bug_files_bug_if_warnings_are_skipped(self):
        exemption = warning_exemption.YamlPackageWarnings(
            package=warning_exemption.Package("foo", "bar"),
            warning_lines=[],
            warning_names=["baz", "qux"],
            observed_on=[],
        )
        self.assertIsNotNone(
            fweb.format_bug_for_mage_followup(
                parent_bug=1,
                crostc_contact="foo",
                per_package_warnings=[
                    dataclasses.replace(
                        exemption,
                        warning_names=["baz"],
                    )
                ],
                frozen_per_package_warnings=[exemption],
            )
        )


class FindEbuildDirMetadataTest(test_helpers.TempDirTestCase):
    """Tests for ebuild DIR_METADATA location."""

    @staticmethod
    def find_dirmd_candidates(
        tempdir: Path,
        package: warning_exemption.Package,
        ebuild_contents: str,
        remote_to_local_map: dict[str, str],
    ) -> list[Path]:
        ebuild_path = (
            tempdir
            / "overlay"
            / str(package)
            / f"{package.package_name}.ebuild"
        )
        ebuild_path.parent.mkdir(parents=True)
        ebuild_path.write_text(ebuild_contents)
        repo_list = fweb.RepoList(tempdir, remote_to_local_map)
        return list(
            fweb.find_ebuild_dir_metadata_candidates(repo_list, ebuild_path)
        )

    @staticmethod
    def ensure_exists(dirs: Iterable[Path] = (), files: Iterable[Path] = ()):
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.touch()

    def test_empty_ebuild_only_searches_parents(self):
        tempdir = self.make_tempdir()
        overlay = tempdir / "overlay"
        candidates = self.find_dirmd_candidates(
            tempdir=tempdir,
            package=warning_exemption.Package("foo", "bar"),
            ebuild_contents="",
            remote_to_local_map={},
        )
        self.assertEqual(
            candidates,
            [
                overlay / "foo" / "bar",
                overlay / "foo",
            ],
        )

    def test_ebuild_with_homepage_searches_homepage(self):
        tempdir = self.make_tempdir()
        overlay = tempdir / "overlay"

        homepage_repo = tempdir / "homepage_repo"
        homepage_file = homepage_repo / "repo" / "path" / "homepage.md"
        self.ensure_exists(
            files=(homepage_file, homepage_repo / ".git"),
        )

        homepage_line = (
            'HOMEPAGE="https://chromium.googlesource.com/chromiumos/some/'
            'repo/path/homepage.md"'
        )
        candidates = self.find_dirmd_candidates(
            tempdir=tempdir,
            package=warning_exemption.Package("foo", "bar"),
            ebuild_contents=homepage_line,
            remote_to_local_map={
                "chromiumos/some": "homepage_repo",
            },
        )
        self.assertEqual(
            candidates,
            [
                overlay / "foo" / "bar",
                homepage_repo / "repo" / "path",
                homepage_repo / "repo",
                homepage_repo,
                overlay / "foo",
            ],
        )

    def test_ebuild_with_subtree_searches_subtree(self):
        tempdir = self.make_tempdir()
        overlay = tempdir / "overlay"

        project_repo = tempdir / "p"
        subtree_path = project_repo / "subtree" / "path"
        self.ensure_exists(
            dirs=(subtree_path / "subdir",),
            files=(project_repo / ".git",),
        )

        candidates = self.find_dirmd_candidates(
            tempdir=tempdir,
            package=warning_exemption.Package("foo", "bar"),
            ebuild_contents=textwrap.dedent(
                """\
                CROS_WORKON_PROJECT="chromiumos/project"
                CROS_WORKON_SUBTREE="subtree/path"
                """
            ),
            remote_to_local_map={
                "chromiumos/project": "p",
            },
        )
        self.assertEqual(
            candidates,
            [
                overlay / "foo" / "bar",
                subtree_path,
                project_repo / "subtree",
                project_repo,
                overlay / "foo",
            ],
        )

    def test_ebuild_with_many_subtrees_skips_subtrees(self):
        tempdir = self.make_tempdir()
        overlay = tempdir / "overlay"

        project_repo = tempdir / "p"
        subtree_path = project_repo / "subtree" / "path"
        self.ensure_exists(
            dirs=(subtree_path / "subdir",),
            files=(project_repo / ".git",),
        )

        candidates = self.find_dirmd_candidates(
            tempdir=tempdir,
            package=warning_exemption.Package("foo", "bar"),
            ebuild_contents=textwrap.dedent(
                """\
                CROS_WORKON_PROJECT="chromiumos/project"
                CROS_WORKON_SUBTREE="subtree/path subtree"
                """
            ),
            remote_to_local_map={
                "chromiumos/project": "p",
            },
        )
        self.assertEqual(
            candidates,
            [
                overlay / "foo" / "bar",
                overlay / "foo",
            ],
        )

    def test_ebuild_searches_platform_subdir_regardless_of_subtree(self):
        tempdir = self.make_tempdir()
        overlay = tempdir / "overlay"

        project_repo = tempdir / "p"
        subtree_path = project_repo / "subtree" / "path"
        self.ensure_exists(
            dirs=(subtree_path / "subdir",),
            files=(project_repo / ".git",),
        )

        candidates = self.find_dirmd_candidates(
            tempdir=tempdir,
            package=warning_exemption.Package("foo", "bar"),
            ebuild_contents=textwrap.dedent(
                """\
                CROS_WORKON_PROJECT="chromiumos/project"
                CROS_WORKON_SUBTREE="subtree/path subtree"
                PLATFORM_SUBDIR="subtree"
                """
            ),
            remote_to_local_map={
                "chromiumos/project": "p",
            },
        )
        self.assertEqual(
            candidates,
            [
                overlay / "foo" / "bar",
                project_repo / "subtree",
                project_repo,
                overlay / "foo",
            ],
        )

    def test_ebuild_with_no_subtree_only_searches_root(self):
        tempdir = self.make_tempdir()
        overlay = tempdir / "overlay"

        project_repo = tempdir / "p"
        self.ensure_exists(
            files=(project_repo / ".git",),
        )

        candidates = self.find_dirmd_candidates(
            tempdir=tempdir,
            package=warning_exemption.Package("foo", "bar"),
            ebuild_contents='CROS_WORKON_PROJECT="chromiumos/project"',
            remote_to_local_map={
                "chromiumos/project": "p",
            },
        )
        self.assertEqual(
            candidates,
            [
                overlay / "foo" / "bar",
                project_repo,
                overlay / "foo",
            ],
        )


if __name__ == "__main__":
    unittest.main()
