# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for llvm_next_py_autoupdate."""

import subprocess
import textwrap
from typing import Iterable
from unittest import mock

from llvm_tools import cros_cls
from llvm_tools import llvm_next_py_autoupdate
from llvm_tools import test_helpers


ARBITRARY_CL_URL = cros_cls.ChangeListURL.parse("crrev.com/c/98765432/1")


class Test(test_helpers.TempDirTestCase):
    """Tests for llvm_next_py_autoupdate."""

    def toolchain_owners_with_listing(self, owners: Iterable[str]) -> set[str]:
        return set(owners)

    def empty_toolchain_owners(self) -> set[str]:
        return self.toolchain_owners_with_listing(())

    def test_compute_new_urls_clears_all_if_manifest_closed(self) -> None:
        manifest_cl = cros_cls.ChangeListURL.parse("crrev.com/c/123/1")
        new_manifest, new_allowlist = llvm_next_py_autoupdate.compute_new_urls(
            manifest_cl,
            is_manifest_closed=True,
            all_changes=[],
            owners=[],
            current_allowlist_urls=[],
        )
        self.assertIsNone(new_manifest)
        self.assertEqual(new_allowlist, [])

    def test_compute_new_urls_updates_manifest_if_untrusted_new_patchset(
        self,
    ) -> None:
        manifest_cl = cros_cls.ChangeListURL.parse("crrev.com/c/123/1")
        owners = ["owner@google.com"]

        main_cl_change = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/123/2"),
            uploader="stranger@evil.com",
        )

        new_manifest, new_allowlist = llvm_next_py_autoupdate.compute_new_urls(
            manifest_cl,
            is_manifest_closed=False,
            all_changes=[main_cl_change],
            owners=owners,
            current_allowlist_urls=[],
        )
        self.assertEqual(new_manifest, "https://crrev.com/c/123/2")
        self.assertEqual(new_allowlist, [])

    def test_compute_new_urls_skips_manifest_update_if_trusted_new_patchset(
        self,
    ) -> None:
        manifest_cl = cros_cls.ChangeListURL.parse("crrev.com/c/123/1")
        owners = ["owner@google.com"]

        main_cl_change = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/123/2"),
            uploader="owner@google.com",
        )

        new_manifest, new_allowlist = llvm_next_py_autoupdate.compute_new_urls(
            manifest_cl,
            is_manifest_closed=False,
            all_changes=[main_cl_change],
            owners=owners,
            current_allowlist_urls=[],
        )
        self.assertEqual(new_manifest, "https://crrev.com/c/123/1")
        self.assertEqual(new_allowlist, [])

    def test_compute_new_urls_populates_allowlist_urls_with_untrusted_deps(
        self,
    ) -> None:
        manifest_cl = cros_cls.ChangeListURL.parse("crrev.com/c/123/1")
        owners = ["owner@google.com"]

        main_cl_change = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/123/1"),
            uploader="owner@google.com",
        )
        untrusted_dep = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/333/1"),
            uploader="stranger@evil.com",
        )

        new_manifest, new_allowlist = llvm_next_py_autoupdate.compute_new_urls(
            manifest_cl,
            is_manifest_closed=False,
            all_changes=[main_cl_change, untrusted_dep],
            owners=owners,
            current_allowlist_urls=[],
        )
        self.assertEqual(new_manifest, "https://crrev.com/c/123/1")
        self.assertEqual(new_allowlist, ["https://crrev.com/c/333/1"])

    def test_compute_new_urls_sorts_allowlist_urls(self) -> None:
        manifest_cl = cros_cls.ChangeListURL.parse("crrev.com/c/123/1")
        owners = ["owner@google.com"]

        main_cl_change = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/123/1"),
            uploader="owner@google.com",
        )
        untrusted_dep1 = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/333/1"),
            uploader="stranger@evil.com",
        )
        untrusted_dep2 = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/222/1"),
            uploader="stranger@evil.com",
        )
        untrusted_dep3 = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/111/1"),
            uploader="stranger@evil.com",
        )

        new_manifest, new_allowlist = llvm_next_py_autoupdate.compute_new_urls(
            manifest_cl,
            is_manifest_closed=False,
            all_changes=[
                main_cl_change,
                untrusted_dep1,
                untrusted_dep2,
                untrusted_dep3,
            ],
            owners=owners,
            current_allowlist_urls=(
                cros_cls.ChangeListURL.parse("crrev.com/c/222/1"),
            ),
        )

        self.assertEqual(new_manifest, "https://crrev.com/c/123/1")
        self.assertEqual(
            new_allowlist,
            [
                "https://crrev.com/c/222/1",
                "https://crrev.com/c/111/1",
                "https://crrev.com/c/333/1",
            ],
        )

    def test_compute_new_urls_sorts_manual_urls(self) -> None:
        manifest_cl = cros_cls.ChangeListURL.parse("crrev.com/c/123/1")
        owners = ["owner@google.com"]

        main_cl_change = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/123/1"),
            uploader="owner@google.com",
        )
        untrusted_dep1 = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/333/1"),
            uploader="stranger@evil.com",
        )
        untrusted_dep2 = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/222/1"),
            uploader="stranger@evil.com",
        )
        untrusted_dep3 = cros_cls.GerritChange(
            url=cros_cls.ChangeListURL.parse("crrev.com/c/111/1"),
            uploader="stranger@evil.com",
        )

        new_manifest, new_allowlist = llvm_next_py_autoupdate.compute_new_urls(
            manifest_cl,
            is_manifest_closed=False,
            all_changes=[
                main_cl_change,
                untrusted_dep1,
                untrusted_dep2,
                untrusted_dep3,
            ],
            owners=owners,
            current_allowlist_urls=(
                cros_cls.ChangeListURL.parse("crrev.com/c/222/1"),
            ),
        )

        self.assertEqual(new_manifest, "https://crrev.com/c/123/1")
        self.assertEqual(
            new_allowlist,
            [
                "https://crrev.com/c/222/1",
                "https://crrev.com/c/111/1",
                "https://crrev.com/c/333/1",
            ],
        )

    def assert_only_call_is_cros_format(
        self, mock_subprocess_run: mock.MagicMock
    ) -> None:
        mock_subprocess_run.assert_called_once()
        self.assertEqual(
            mock_subprocess_run.call_args[0][0][:2],
            ("cros", "format"),
        )

    @mock.patch.object(subprocess, "run")
    def test_updating_cl_list_to_be_empty(
        self, mock_subprocess_run: mock.MagicMock
    ) -> None:
        llvm_next_py = self.make_tempdir() / "llvm_next.py"
        llvm_next_py.write_text(
            textwrap.dedent(
                """\
                # Some comment
                _LLVM_NEXT_MANIFEST_CL: str | None = "some CL URL"
                _LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = (
                "some other CL URL",
                )

                # Some other comment
                """
            ),
            encoding="utf-8",
        )

        llvm_next_py_autoupdate.write_url_list(llvm_next_py, None, [])
        self.assertEqual(
            llvm_next_py.read_text(encoding="utf-8"),
            textwrap.dedent(
                """\
                # Some comment
                _LLVM_NEXT_MANIFEST_CL: str | None = None
                _LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = (
                )

                # Some other comment
                """
            ),
        )
        self.assert_only_call_is_cros_format(mock_subprocess_run)

    @mock.patch.object(subprocess, "run")
    def test_adding_to_empty_cl_list(
        self, mock_subprocess_run: mock.MagicMock
    ) -> None:
        llvm_next_py = self.make_tempdir() / "llvm_next.py"
        llvm_next_py.write_text(
            textwrap.dedent(
                """\
                # Some comment
                _LLVM_NEXT_MANIFEST_CL: str | None = None
                _LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = ()

                # Some other comment
                """
            ),
            encoding="utf-8",
        )

        llvm_next_py_autoupdate.write_url_list(llvm_next_py, None, ["url1"])
        self.assertEqual(
            llvm_next_py.read_text(encoding="utf-8"),
            textwrap.dedent(
                """\
                # Some comment
                _LLVM_NEXT_MANIFEST_CL: str | None = None
                _LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = (
                'url1',
                )

                # Some other comment
                """
            ),
        )
        self.assert_only_call_is_cros_format(mock_subprocess_run)

    @mock.patch.object(subprocess, "run")
    def test_adding_to_non_empty_cl_list(
        self, mock_subprocess_run: mock.MagicMock
    ) -> None:
        llvm_next_py = self.make_tempdir() / "llvm_next.py"
        llvm_next_py.write_text(
            textwrap.dedent(
                """\
                # Some comment
                _LLVM_NEXT_MANIFEST_CL: str | None = None
                _LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = (
                'url1',
                )

                # Some other comment
                """
            ),
            encoding="utf-8",
        )

        llvm_next_py_autoupdate.write_url_list(
            llvm_next_py, None, ["url1", "url2"]
        )
        self.assertEqual(
            llvm_next_py.read_text(encoding="utf-8"),
            textwrap.dedent(
                """\
                # Some comment
                _LLVM_NEXT_MANIFEST_CL: str | None = None
                _LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = (
                'url1',
                'url2',

                )

                # Some other comment
                """
            ),
        )
        self.assert_only_call_is_cros_format(mock_subprocess_run)

    @mock.patch.object(subprocess, "run")
    def test_same_line_cl_paren_works(
        self, mock_subprocess_run: mock.MagicMock
    ) -> None:
        llvm_next_py = self.make_tempdir() / "llvm_next.py"
        llvm_next_py.write_text(
            textwrap.dedent(
                """\
                # Some comment
                _LLVM_NEXT_MANIFEST_CL: str | None = None
                _LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = ("some URL")

                # Some other comment
                """
            ),
            encoding="utf-8",
        )

        llvm_next_py_autoupdate.write_url_list(llvm_next_py, None, [])
        self.assertEqual(
            llvm_next_py.read_text(encoding="utf-8"),
            textwrap.dedent(
                """\
                # Some comment
                _LLVM_NEXT_MANIFEST_CL: str | None = None
                _LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = ()

                # Some other comment
                """
            ),
        )
        self.assert_only_call_is_cros_format(mock_subprocess_run)
