# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for retrieving the LLVM hash."""

from pathlib import Path
import subprocess
import textwrap
from typing import Optional
from unittest import mock

from cros_utils import git_utils
from llvm_tools import get_llvm_hash
from llvm_tools import llvm_next
from llvm_tools import test_helpers


# We grab protected stuff from get_llvm_hash. That's OK.
# pylint: disable=protected-access


def mock_run_results(returncode: int, stderr: bytes) -> mock.MagicMock:
    m = mock.MagicMock()
    m.returncode = returncode
    m.stderr = stderr
    return m


class TestGetLLVMHash(test_helpers.TempDirTestCase):
    """The LLVMHash test class."""

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

    @mock.patch.object(get_llvm_hash, "GetCachedUpToDateReadOnlyLLVMRepo")
    @mock.patch.object(git_utils, "maybe_show_file_at_commit")
    def testGetLLVMMajorVersionWithOldPath(
        self,
        mock_show_file_at_commit,
        mock_get_up_to_date_repo,
    ):
        src_dir = self.make_tempdir()
        mock_get_up_to_date_repo.return_value = get_llvm_hash.ReadOnlyLLVMRepo(
            path=src_dir,
            remote="origin",
            upstream_main="main",
        )

        def show_file_at_commit(
            repo: Path, ref: str, path: str
        ) -> Optional[str]:
            self.assertEqual(repo, src_dir)
            self.assertEqual(ref, "HEAD")
            self.assertEqual(path, "llvm/CMakeLists.txt")
            return textwrap.dedent(
                """
                if(NOT DEFINED LLVM_VERSION_MAJOR)
                  set(LLVM_VERSION_MAJOR 12345)
                endif()
                """
            )

        mock_show_file_at_commit.side_effect = show_file_at_commit
        self.assertEqual(get_llvm_hash.GetLLVMMajorVersion(), "12345")

    @mock.patch.object(get_llvm_hash, "GetCachedUpToDateReadOnlyLLVMRepo")
    @mock.patch.object(git_utils, "maybe_show_file_at_commit")
    def testGetLLVMMajorVersionWithNewPath(
        self,
        mock_show_file_at_commit,
        mock_get_up_to_date_repo,
    ):
        src_dir = self.make_tempdir()
        mock_get_up_to_date_repo.return_value = get_llvm_hash.ReadOnlyLLVMRepo(
            path=src_dir,
            remote="origin",
            upstream_main="main",
        )

        def show_file_at_commit(
            repo: Path, ref: str, path: str
        ) -> Optional[str]:
            self.assertEqual(repo, src_dir)
            self.assertEqual(ref, "HEAD")
            if path == "llvm/CMakeLists.txt":
                return textwrap.dedent(
                    """
                    Some text
                    that has
                    nothing to do with
                    LLVM_VERSION_MAJOR
                    """
                )
            self.assertEqual(path, "cmake/Modules/LLVMVersion.cmake")
            return textwrap.dedent(
                """
                if(NOT DEFINED LLVM_VERSION_MAJOR)
                  set(LLVM_VERSION_MAJOR 12345)
                endif()
                """
            )

        mock_show_file_at_commit.side_effect = show_file_at_commit
        self.assertEqual(get_llvm_hash.GetLLVMMajorVersion(), "12345")

    def testGetLLVMNextHash(self):
        self.assertEqual(
            get_llvm_hash.LLVMHash().GetCrOSLLVMNextHash(),
            llvm_next.LLVM_NEXT_HASH,
        )
