#!/usr/bin/env python3
# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for retrieving the LLVM hash."""

import contextlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from unittest import mock

import get_llvm_hash
import llvm_next
import subprocess_helpers


# We grab protected stuff from get_llvm_hash. That's OK.
# pylint: disable=protected-access


def mock_run_results(returncode: int, stderr: bytes) -> mock.MagicMock:
    m = mock.MagicMock()
    m.returncode = returncode
    m.stderr = stderr
    return m


class TestGetLLVMHash(unittest.TestCase):
    """The LLVMHash test class."""

    def make_tempdir(self):
        d = Path(tempfile.mkdtemp(prefix="get_llvm_hash_unittest_"))
        self.addCleanup(shutil.rmtree, d)
        return d

    def setUp(self):
        # We mock out quite a bit. Ensure every test is self-contained.
        get_llvm_hash.GetLLVMMajorVersion.cache_clear()

    @mock.patch.object(subprocess, "run")
    def testCloneRepoSucceedsWhenGitSucceeds(self, run_mock):
        run_mock.return_value = mock_run_results(returncode=0, stderr=b"")
        llvm_hash = get_llvm_hash.LLVMHash()

        into_tempdir = "/tmp/tmpTest"
        llvm_hash.CloneLLVMRepo(into_tempdir)
        run_mock.assert_called_with(
            ["git", "clone", get_llvm_hash._LLVM_GIT_URL, into_tempdir],
            check=False,
            stderr=subprocess.PIPE,
        )

    @mock.patch.object(subprocess, "run")
    def testCloneRepoFailsWhenGitFails(self, run_mock):
        run_mock.return_value = mock_run_results(
            returncode=1, stderr=b"some stderr"
        )

        with self.assertRaisesRegex(ValueError, "Failed to clone.*some stderr"):
            get_llvm_hash.LLVMHash().CloneLLVMRepo("/tmp/tmp1")

    @mock.patch.object(get_llvm_hash, "GetGitHashFrom")
    def testGetGitHashWorks(self, mock_get_git_hash):
        mock_get_git_hash.return_value = "a13testhash2"

        self.assertEqual(
            get_llvm_hash.GetGitHashFrom("/tmp/tmpTest", 100), "a13testhash2"
        )

        mock_get_git_hash.assert_called_once()

    @mock.patch.object(get_llvm_hash.LLVMHash, "GetLLVMHash")
    @mock.patch.object(get_llvm_hash, "GetGoogle3LLVMVersion")
    def testReturnGoogle3LLVMHash(
        self, mock_google3_llvm_version, mock_get_llvm_hash
    ):
        mock_get_llvm_hash.return_value = "a13testhash3"
        mock_google3_llvm_version.return_value = 1000
        self.assertEqual(
            get_llvm_hash.LLVMHash().GetGoogle3LLVMHash(), "a13testhash3"
        )
        mock_get_llvm_hash.assert_called_once_with(1000)

    @mock.patch.object(get_llvm_hash.LLVMHash, "GetLLVMHash")
    @mock.patch.object(get_llvm_hash, "GetGoogle3LLVMVersion")
    def testReturnGoogle3UnstableLLVMHash(
        self, mock_google3_llvm_version, mock_get_llvm_hash
    ):
        mock_get_llvm_hash.return_value = "a13testhash3"
        mock_google3_llvm_version.return_value = 1000
        self.assertEqual(
            get_llvm_hash.LLVMHash().GetGoogle3UnstableLLVMHash(),
            "a13testhash3",
        )
        mock_get_llvm_hash.assert_called_once_with(1000)

    @mock.patch.object(subprocess, "check_output")
    def testSuccessfullyGetGitHashFromToTOfLLVM(self, mock_check_output):
        mock_check_output.return_value = "a123testhash1 path/to/main\n"
        self.assertEqual(
            get_llvm_hash.LLVMHash().GetTopOfTrunkGitHash(), "a123testhash1"
        )
        mock_check_output.assert_called_once()

    @mock.patch.object(subprocess, "Popen")
    def testCheckoutBranch(self, mock_popen):
        mock_popen.return_value = contextlib.nullcontext(
            mock.MagicMock(communicate=lambda: (None, None), returncode=0)
        )
        get_llvm_hash.CheckoutBranch("fake/src_dir", "fake_branch")
        self.assertEqual(
            mock_popen.call_args_list[0][0],
            (["git", "-C", "fake/src_dir", "checkout", "fake_branch"],),
        )
        self.assertEqual(
            mock_popen.call_args_list[1][0],
            (["git", "-C", "fake/src_dir", "pull"],),
        )

    def testParseLLVMMajorVersion(self):
        cmakelist_42 = (
            "set(CMAKE_BUILD_WITH_INSTALL_NAME_DIR ON)\n"
            "if(NOT DEFINED LLVM_VERSION_MAJOR)\n"
            "  set(LLVM_VERSION_MAJOR 42)\n"
            "endif()"
        )
        self.assertEqual(
            get_llvm_hash.ParseLLVMMajorVersion(cmakelist_42), "42"
        )

    def testParseLLVMMajorVersionInvalid(self):
        invalid_cmakelist = "invalid cmakelist.txt contents"
        self.assertIsNone(
            get_llvm_hash.ParseLLVMMajorVersion(invalid_cmakelist)
        )

    @mock.patch.object(get_llvm_hash, "GetAndUpdateLLVMProjectInLLVMTools")
    @mock.patch.object(subprocess_helpers, "CheckCommand")
    def testGetLLVMMajorVersionWithOldPath(
        self,
        _mock_check_command,
        mock_update_project,
    ):
        src_dir = self.make_tempdir()
        mock_update_project.return_value = str(src_dir)

        cmakelists = Path(src_dir) / "llvm" / "CMakeLists.txt"
        cmakelists.parent.mkdir(parents=True)
        cmakelists.write_text(
            textwrap.dedent(
                """
                if(NOT DEFINED LLVM_VERSION_MAJOR)
                  set(LLVM_VERSION_MAJOR 12345)
                endif()
                """
            ),
            encoding="utf-8",
        )
        self.assertEqual(get_llvm_hash.GetLLVMMajorVersion(), "12345")

    @mock.patch.object(get_llvm_hash, "GetAndUpdateLLVMProjectInLLVMTools")
    @mock.patch.object(subprocess_helpers, "CheckCommand")
    def testGetLLVMMajorVersionWithNewPath(
        self,
        _mock_check_command,
        mock_update_project,
    ):
        src_dir = self.make_tempdir()
        mock_update_project.return_value = str(src_dir)

        old_cmakelists = Path(src_dir) / "llvm" / "CMakeLists.txt"
        old_cmakelists.parent.mkdir(parents=True)
        old_cmakelists.write_text(
            textwrap.dedent(
                """
                Some text
                that has
                nothing to do with
                LLVM_VERSION_MAJOR
                """
            ),
            encoding="utf-8",
        )

        new_cmakelists = (
            Path(src_dir) / "cmake" / "Modules" / "LLVMVersion.cmake"
        )
        new_cmakelists.parent.mkdir(parents=True)
        new_cmakelists.write_text(
            textwrap.dedent(
                """
                if(NOT DEFINED LLVM_VERSION_MAJOR)
                  set(LLVM_VERSION_MAJOR 5432)
                endif()
                """
            ),
            encoding="utf-8",
        )

        self.assertEqual(get_llvm_hash.GetLLVMMajorVersion(), "5432")

    def testGetLLVMNextHash(self):
        self.assertEqual(
            get_llvm_hash.LLVMHash().GetCrOSLLVMNextHash(),
            llvm_next.LLVM_NEXT_HASH,
        )


if __name__ == "__main__":
    unittest.main()
