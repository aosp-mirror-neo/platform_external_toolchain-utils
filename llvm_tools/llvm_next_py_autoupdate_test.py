# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for llvm_next_py_autoupdate."""

import contextlib
import dataclasses
import json
import subprocess
import textwrap
from typing import Dict, Iterable
from unittest import mock

from llvm_tools import cros_cls
from llvm_tools import llvm_next_py_autoupdate
from llvm_tools import test_helpers


ARBITRARY_CL_URL = cros_cls.ChangeListURL.parse("crrev.com/c/98765432/1")


class Test(test_helpers.TempDirTestCase):
    """Tests for llvm_next_py_autoupdate."""

    def toolchain_owners_with_listing(
        self, owners: Iterable[str]
    ) -> llvm_next_py_autoupdate.LazyToolchainOwners:
        owners_file_path = self.make_tempdir() / "OWNERS.mock"
        owners_file_path.write_text("\n".join(owners), encoding="utf-8")
        return llvm_next_py_autoupdate.LazyToolchainOwners(owners_file_path)

    def empty_toolchain_owners(
        self,
    ) -> llvm_next_py_autoupdate.LazyToolchainOwners:
        return self.toolchain_owners_with_listing(())

    @mock.patch.object(subprocess, "run")
    def test_fetch_cl_info_works_with_new_cl(self, mock_subprocess_run):
        mock_run_return_value = mock.MagicMock()
        mock_run_return_value.stdout = json.dumps(
            [
                {
                    "status": "NEW",
                    "currentPatchSet": {
                        "number": "123",
                    },
                }
            ]
        )
        mock_subprocess_run.return_value = mock_run_return_value
        self.assertEqual(
            llvm_next_py_autoupdate.fetch_cl_info(
                self.empty_toolchain_owners(), ARBITRARY_CL_URL
            ),
            llvm_next_py_autoupdate.GerritCLInfo(
                is_abandoned_or_merged=False,
                is_uploader_a_googler=False,
                most_recent_patch_set=123,
            ),
        )

    @mock.patch.object(subprocess, "run")
    def test_fetch_cl_info_works_with_closed_cl(self, mock_subprocess_run):
        mock_run_return_value = mock.MagicMock()
        mock_subprocess_run.return_value = mock_run_return_value

        for closed_status in ("ABANDONED", "MERGED"):
            mock_run_return_value.stdout = json.dumps(
                [
                    {
                        "status": closed_status,
                        "currentPatchSet": {
                            "number": "123",
                        },
                    }
                ]
            )
            self.assertEqual(
                llvm_next_py_autoupdate.fetch_cl_info(
                    self.empty_toolchain_owners(), ARBITRARY_CL_URL
                ),
                llvm_next_py_autoupdate.GerritCLInfo(
                    is_abandoned_or_merged=True,
                    is_uploader_a_googler=False,
                    most_recent_patch_set=123,
                ),
            )

    @mock.patch.object(subprocess, "run")
    def test_fetch_cl_info_determines_googler_is_googler(
        self, mock_subprocess_run
    ):
        mock_run_return_value = mock.MagicMock()
        mock_run_return_value.stdout = json.dumps(
            [
                {
                    "status": "NEW",
                    "currentPatchSet": {
                        "number": "123",
                        "uploader": {
                            "email": "foo@google.com",
                        },
                    },
                }
            ]
        )
        mock_subprocess_run.return_value = mock_run_return_value
        self.assertEqual(
            llvm_next_py_autoupdate.fetch_cl_info(
                self.empty_toolchain_owners(), ARBITRARY_CL_URL
            ),
            llvm_next_py_autoupdate.GerritCLInfo(
                is_abandoned_or_merged=False,
                is_uploader_a_googler=True,
                most_recent_patch_set=123,
            ),
        )

    @mock.patch.object(subprocess, "run")
    def test_fetch_cl_info_determines_chromium_isnt_googler(
        self, mock_subprocess_run
    ):
        mock_run_return_value = mock.MagicMock()
        mock_run_return_value.stdout = json.dumps(
            [
                {
                    "status": "NEW",
                    "currentPatchSet": {
                        "number": "123",
                        "uploader": {
                            "email": "foo@chromium.org",
                        },
                    },
                }
            ]
        )
        mock_subprocess_run.return_value = mock_run_return_value
        self.assertEqual(
            llvm_next_py_autoupdate.fetch_cl_info(
                self.empty_toolchain_owners(), ARBITRARY_CL_URL
            ),
            llvm_next_py_autoupdate.GerritCLInfo(
                is_abandoned_or_merged=False,
                is_uploader_a_googler=False,
                most_recent_patch_set=123,
            ),
        )

    @mock.patch.object(subprocess, "run")
    def test_fetch_cl_info_determines_chromium_owner_is_googler(
        self, mock_subprocess_run
    ):
        mock_run_return_value = mock.MagicMock()
        mock_run_return_value.stdout = json.dumps(
            [
                {
                    "status": "NEW",
                    "currentPatchSet": {
                        "number": "123",
                        "uploader": {
                            "email": "foo@chromium.org",
                        },
                    },
                }
            ]
        )
        mock_subprocess_run.return_value = mock_run_return_value
        self.assertEqual(
            llvm_next_py_autoupdate.fetch_cl_info(
                self.toolchain_owners_with_listing(
                    ["bar@google.com", "foo@chromium.org"]
                ),
                ARBITRARY_CL_URL,
            ),
            llvm_next_py_autoupdate.GerritCLInfo(
                is_abandoned_or_merged=False,
                is_uploader_a_googler=True,
                most_recent_patch_set=123,
            ),
        )

    @contextlib.contextmanager
    def mock_fetch_cl_info(
        self,
        mock_cl_info: Dict[
            cros_cls.ChangeListURL, llvm_next_py_autoupdate.GerritCLInfo
        ],
    ):
        """Mocks `fetch_cl_info` to return `mock_cl_info` entries."""

        def fetch_cl_info_side_effect(
            _owners: llvm_next_py_autoupdate.LazyToolchainOwners,
            cl: cros_cls.ChangeListURL,
        ) -> llvm_next_py_autoupdate.GerritCLInfo:
            if x := mock_cl_info.get(cl):
                return x
            raise ValueError(f"CL without mock info: {cl}")

        with mock.patch.object(
            llvm_next_py_autoupdate, "fetch_cl_info"
        ) as mock_fetch_cl_info:
            mock_fetch_cl_info.side_effect = fetch_cl_info_side_effect
            yield mock_fetch_cl_info

    def test_update_empty_urls(self):
        with self.mock_fetch_cl_info(mock_cl_info={}):
            self.assertIsNone(
                llvm_next_py_autoupdate.update_testing_url_list(
                    self.empty_toolchain_owners(), ()
                )
            )

    def test_merged_cl_is_removed_by_update(self):
        mock_cl_info = {
            ARBITRARY_CL_URL: llvm_next_py_autoupdate.GerritCLInfo(
                is_abandoned_or_merged=True,
                is_uploader_a_googler=True,
                most_recent_patch_set=1,
            )
        }
        with self.mock_fetch_cl_info(mock_cl_info) as mocked_fetch:
            (
                messages,
                new_list,
            ) = llvm_next_py_autoupdate.update_testing_url_list(
                self.empty_toolchain_owners(), [str(ARBITRARY_CL_URL)]
            )
            mocked_fetch.assert_called_once()

        self.assertEqual(new_list, [])
        self.assertNotEqual(messages, "")

    def test_update_is_nop_if_no_CLs_changed(self):
        mock_cl_info = {
            ARBITRARY_CL_URL: llvm_next_py_autoupdate.GerritCLInfo(
                is_abandoned_or_merged=False,
                is_uploader_a_googler=True,
                most_recent_patch_set=ARBITRARY_CL_URL.patch_set,
            ),
        }
        with self.mock_fetch_cl_info(mock_cl_info) as mocked_fetch:
            self.assertIsNone(
                llvm_next_py_autoupdate.update_testing_url_list(
                    self.empty_toolchain_owners(), [str(ARBITRARY_CL_URL)]
                )
            )
            mocked_fetch.assert_called_once()

    def test_update_happens_if_patch_set_changed(self):
        new_patch_set = ARBITRARY_CL_URL.patch_set + 1
        mock_cl_info = {
            ARBITRARY_CL_URL: llvm_next_py_autoupdate.GerritCLInfo(
                is_abandoned_or_merged=False,
                is_uploader_a_googler=True,
                most_recent_patch_set=new_patch_set,
            ),
        }
        with self.mock_fetch_cl_info(mock_cl_info) as mocked_fetch:
            (
                messages,
                new_list,
            ) = llvm_next_py_autoupdate.update_testing_url_list(
                self.empty_toolchain_owners(), [str(ARBITRARY_CL_URL)]
            )
            mocked_fetch.assert_called_once()

        self.assertEqual(
            new_list,
            [
                str(
                    dataclasses.replace(
                        ARBITRARY_CL_URL,
                        patch_set=new_patch_set,
                    )
                )
            ],
        )
        self.assertNotEqual(messages, "")

    def test_update_skipped_if_patch_set_changed_by_non_googler(self):
        new_patch_set = ARBITRARY_CL_URL.patch_set + 1
        mock_cl_info = {
            ARBITRARY_CL_URL: llvm_next_py_autoupdate.GerritCLInfo(
                is_abandoned_or_merged=False,
                is_uploader_a_googler=False,
                most_recent_patch_set=new_patch_set,
            ),
        }
        with self.mock_fetch_cl_info(mock_cl_info) as mocked_fetch:
            self.assertIsNone(
                llvm_next_py_autoupdate.update_testing_url_list(
                    self.empty_toolchain_owners(), [str(ARBITRARY_CL_URL)]
                )
            )
            mocked_fetch.assert_called_once()

    def assert_only_call_is_cros_format(
        self, mock_subprocess_run: mock.MagicMock
    ):
        mock_subprocess_run.assert_called_once()
        self.assertEqual(
            mock_subprocess_run.call_args[0][0][:2],
            ("cros", "format"),
        )

    @mock.patch.object(subprocess, "run")
    def test_updating_empty_cl_list(self, mock_subprocess_run):
        llvm_next_py = self.make_tempdir() / "llvm_next.py"
        llvm_next_py.write_text(
            textwrap.dedent(
                """\
                # Some comment
                LLVM_NEXT_TESTING_CL_URLS: Iterable[str] = ()

                # Some other comment
                """
            ),
            encoding="utf-8",
        )

        llvm_next_py_autoupdate.write_url_list(
            llvm_next_py, [str(ARBITRARY_CL_URL)]
        )
        self.assertEqual(
            llvm_next_py.read_text(encoding="utf-8"),
            textwrap.dedent(
                f"""\
                # Some comment
                LLVM_NEXT_TESTING_CL_URLS: Iterable[str] = (
                {repr(str(ARBITRARY_CL_URL))},
                )

                # Some other comment
                """
            ),
        )
        self.assert_only_call_is_cros_format(mock_subprocess_run)

    @mock.patch.object(subprocess, "run")
    def test_updating_cl_list_to_be_empty(self, mock_subprocess_run):
        llvm_next_py = self.make_tempdir() / "llvm_next.py"
        llvm_next_py.write_text(
            textwrap.dedent(
                """\
                # Some comment
                LLVM_NEXT_TESTING_CL_URLS: Iterable[str] = (
                "some CL URL",
                )

                # Some other comment
                """
            ),
            encoding="utf-8",
        )

        llvm_next_py_autoupdate.write_url_list(llvm_next_py, [])
        # N.B., `cros format` will eliminate the unnecesary '\n's.
        self.assertEqual(
            llvm_next_py.read_text(encoding="utf-8"),
            textwrap.dedent(
                """\
                # Some comment
                LLVM_NEXT_TESTING_CL_URLS: Iterable[str] = (


                )

                # Some other comment
                """
            ),
        )
        self.assert_only_call_is_cros_format(mock_subprocess_run)

    @mock.patch.object(subprocess, "run")
    def test_same_line_cl_paren_works(self, mock_subprocess_run):
        llvm_next_py = self.make_tempdir() / "llvm_next.py"
        llvm_next_py.write_text(
            textwrap.dedent(
                """\
                # Some comment
                LLVM_NEXT_TESTING_CL_URLS: Iterable[str] = ("some CL URL")

                # Some other comment
                """
            ),
            encoding="utf-8",
        )

        llvm_next_py_autoupdate.write_url_list(llvm_next_py, [])
        # N.B., `cros format` will eliminate the unnecesary '\n'.
        self.assertEqual(
            llvm_next_py.read_text(encoding="utf-8"),
            textwrap.dedent(
                """\
                # Some comment
                LLVM_NEXT_TESTING_CL_URLS: Iterable[str] = (

                )

                # Some other comment
                """
            ),
        )
        self.assert_only_call_is_cros_format(mock_subprocess_run)

    def test_owners_file_parsing_functions(self):
        contents = textwrap.dedent(
            """\
            foo@chromium.org
            bar@google.com
            """
        )
        owners = llvm_next_py_autoupdate.parse_direct_owners_from_file(contents)
        self.assertEqual(owners, ["foo@chromium.org", "bar@google.com"])

    def test_owners_file_parsing_ignores_exciting_patterns(self):
        contents = textwrap.dedent(
            """\
            # Some commentary
            foo@chromium.org  # More commentary
            #Even-More@Commentary
            per-file some-file = bar@chromium.org
            include ../OWNERS
            # OWNERS emails can either be '*' or a valid email. Ignore the
            # former.
            *
            """
        )
        owners = llvm_next_py_autoupdate.parse_direct_owners_from_file(contents)
        self.assertEqual(owners, ["foo@chromium.org"])
