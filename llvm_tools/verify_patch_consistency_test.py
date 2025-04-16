# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for verify_patch_consistency."""

import dataclasses
import json
import os
from pathlib import Path
import subprocess
import textwrap
from typing import Callable
from unittest import mock

from cros_utils import git_utils
from llvm_tools import git_llvm_rev
from llvm_tools import llvm_project_base_commit
from llvm_tools import patch_utils
from llvm_tools import test_helpers
from llvm_tools import verify_patch_consistency


GERRIT_JSON_FIXTURE = """\
[
  {
    "project": "github.com/llvm/llvm-project",
    "branch": "chromeos/llvm-r516547-1",
    "createdOn": 1718657891,
    "lastUpdated": 1720459316,
    "id": "If250deb1c592b3cf054cca8c0cd530f3d6fd4f89",
    "owner": {
      "name": "User McUserface",
      "email": "someuserhere@google.com",
      "username": "User McUserface"
    },
    "number": "5637483",
    "url": "https://crrev.com/c/5637483",
    "status": "ABANDONED",
    "subject": "Revert \\"add_tablegen: Quick fix to reflect LLVM_TABLEGEN\\"",
    "private": false,
    "topic": null,
    "currentPatchSet": {
      "approvals": [
        {
          "type": "CRVW",
          "description": "Code-Review",
          "value": "0",
          "grantedOn": 1718657891,
          "by": {
            "name": "User McUserface",
            "email": "someuserhere@google.com",
            "username": "User McUserface"
          }
        },
        {
          "type": "COMR",
          "description": "Commit-Queue",
          "value": "0",
          "grantedOn": 1718752263,
          "by": {
            "name": "User McUserface",
            "email": "someuserhere@google.com",
            "username": "User McUserface"
          }
        },
        {
          "type": "VRIF",
          "description": "Verified",
          "value": "0",
          "grantedOn": 1718657891,
          "by": {
            "name": "User McUserface",
            "email": "someuserhere@google.com",
            "username": "User McUserface"
          }
        }
      ],
      "ref": "refs/changes/83/5637483/1",
      "revision": "9f316824661b96d0ba586ff48b6128f7e9783f19",
      "number": "1",
      "date": 1718657800,
      "draft": false
    },
    "commitMessage": "A commit message",
    "dependsOn": [
      {
        "revision": "210497ee293346804b43ece37fb9d6658c39ab34"
      }
    ]
  }
]
"""


@dataclasses.dataclass(frozen=True)
class _RunnerArgs:
    main_sha: str
    patch_branch: str
    toolchain_utils_dir: Path
    llvm_src_dir: Path
    patches_json: Path
    chromiumos_overlay: Path


class TestVerifyPatchConsistency(test_helpers.TempDirTestCase):
    """Test verify_patch_consistency."""

    def __init__(self, *nargs, **kwargs):
        super().__init__(*nargs, **kwargs)
        self.git_utils_patcher = None
        self.git_llvm_rev_patcher = None
        self.verify_patch_consistency_patcher = None

    @mock.patch.object(verify_patch_consistency, "_gerrit_inspect")
    def test_parse_branch_simple(self, mock_gerrit_inspect):
        """Test we're extracting the llvm revision and ref from gerrit json."""

        mock_gerrit_inspect.return_value = [
            {
                "branch": "chromeos/llvm-r1234567-42",
                "currentPatchSet": {"ref": "some_remote_ref"},
            }
        ]
        llvm_rev, ref = verify_patch_consistency.parse_branch(10101, Path())
        self.assertEqual(llvm_rev, 1234567)
        self.assertEqual(ref, "some_remote_ref")

    @mock.patch.object(verify_patch_consistency, "_gerrit_inspect")
    def test_parse_branch_complex(self, mock_gerrit_inspect):
        """Test parse_branch with a real JSON response."""

        mock_gerrit_inspect.return_value = json.loads(GERRIT_JSON_FIXTURE)
        llvm_rev, ref = verify_patch_consistency.parse_branch(5637483, Path())
        self.assertEqual(llvm_rev, 516547)
        self.assertEqual(ref, "refs/changes/83/5637483/1")

    def _set_up_mocking(self, translate_sha: str, fetch_head_ref: str):
        # Patch git_utils ----------------------------------------------
        self.git_utils_patcher = mock.patch.multiple(
            git_utils,
            # Do not allow network calls.
            fetch=lambda *_, **__: None,
            # FETCH_HEAD may not exist, so just mock resolve_ref. It's only
            # used in printing here.
            resolve_ref=lambda *_, **__: "MOCK",
        )
        self.git_utils_patcher.start()

        # Patch git_llvm_rev ------------------------------------------
        self.git_llvm_rev_patcher = mock.patch.multiple(
            git_llvm_rev,
            # Don't actually try to search through the LLVM project
            # git shas. We don't have the known reference SHAs to
            # do that.
            translate_rev_to_sha=lambda _, __: translate_sha,
        )
        self.git_llvm_rev_patcher.start()

        # Mock verify_patch_consistency -------------------------------

        # NOTE:
        # We need to capture the original ref_diff reference here,
        # because otherwise by the time that _mock_ref_diff evaluation
        # actually happens, the original verify_patch_consistency will
        # be fully monkeypatched out, and we'll get infinite recursion.
        ref_diff_capture = verify_patch_consistency.ref_diff

        def _mock_ref_diff(cwd: Path, ref1: str, _: str) -> str:
            return ref_diff_capture(cwd, ref1, fetch_head_ref)

        self.verify_patch_consistency_patcher = mock.patch.multiple(
            verify_patch_consistency,
            ref_diff=_mock_ref_diff,
        )
        self.verify_patch_consistency_patcher.start()

    def _stop_mocking(self):
        if self.verify_patch_consistency_patcher:
            self.verify_patch_consistency_patcher.stop()
        if self.git_llvm_rev_patcher:
            self.git_llvm_rev_patcher.stop()
        if self.git_utils_patcher:
            self.git_utils_patcher.stop()

    def _run_llvm_harness(
        self, tempdir: Path, llvm_svn_revision: int, runner: Callable
    ):
        """Set up for full verification tests."""

        # Set up directories and paths.
        # Current layout is:
        #
        #   toolchain-utils/
        #     OWNERS
        #     OWNERS.toolchain
        #   sys-devel/llvm/
        #     llvm-9999.ebuild
        #   llvm-project/
        #   PATCHES.json
        #   patch.patch
        #
        fake_toolchain_utils = tempdir / "toolchain-utils"
        fake_toolchain_utils.mkdir()
        (fake_toolchain_utils / "OWNERS").touch()
        (fake_toolchain_utils / "OWNERS.toolchain").touch()
        fake_patches_json_path = tempdir / "PATCHES.json"
        fake_chromiumos_overlay = tempdir / "chromiumos-overlay"
        for p in patch_utils.CHROMEOS_LLVM_SUBPACKAGES:
            package_name = os.path.basename(p)
            live_ebuild = (
                fake_chromiumos_overlay / p / f"{package_name}-9999.ebuild"
            )
            live_ebuild.parent.mkdir(parents=True)
            # Always use llvm as the CMAKE_USE_DIR; it's simplest to mock, and
            # we don't gain meaningful additional coverage by varying it.
            live_ebuild.write_text(
                'export CMAKE_USE_DIR="${S}/llvm"\n', encoding="utf-8"
            )
        patch_name = "patch.patch"
        with fake_patches_json_path.open("w", encoding="utf-8") as f:
            json.dump([{"rel_patch_path": patch_name}], f)
        fake_llvm_src_dir = tempdir / "llvm-project"
        fake_llvm_src_dir.mkdir()
        cmake_file = fake_llvm_src_dir / "llvm" / "CMakeLists.txt"
        cmake_file.parent.mkdir()
        cmake_file.touch()

        # Set up git state.
        # There's a lot going on here, but it mostly sets up commits
        # like so:
        #
        #   a main
        #    \
        #     -> b -> c patch_branch
        #
        # where
        #   a = Initial commit
        #   b = ChromeOS Base commit
        #   c = Hello World Commit
        subprocess.run(
            ["git", "init", "-b", "main", "-q"],
            cwd=fake_llvm_src_dir,
            check=True,
        )
        a_file = fake_llvm_src_dir / "a.txt"
        a_file.touch()
        git_utils.commit_all_changes(fake_llvm_src_dir, "Initial commit")
        main_sha = subprocess.run(
            ["git", "log", "-n1", "--format=%H"],
            check=True,
            cwd=fake_llvm_src_dir,
            stdout=subprocess.PIPE,
            encoding="utf-8",
        ).stdout.strip()
        patch_branch = "patch_branch"
        subprocess.run(
            ["git", "switch", "-C", patch_branch],
            cwd=fake_llvm_src_dir,
            check=True,
        )
        llvm_project_base_commit.make_base_commit(
            fake_toolchain_utils,
            fake_llvm_src_dir,
            llvm_svn_revision,
            chromiumos_overlay=fake_chromiumos_overlay,
        )
        a_file.write_text("hello world", encoding="utf-8")
        git_utils.commit_all_changes(fake_llvm_src_dir, "Hello world commit")
        diff = git_utils.format_patch(fake_llvm_src_dir, "HEAD")
        subprocess.run(
            ["git", "switch", "-C", "main"], cwd=fake_llvm_src_dir, check=True
        )
        (tempdir / patch_name).write_text(diff, encoding="utf-8")
        runner(
            _RunnerArgs(
                main_sha=main_sha,
                patch_branch=patch_branch,
                toolchain_utils_dir=fake_toolchain_utils,
                llvm_src_dir=fake_llvm_src_dir,
                patches_json=fake_patches_json_path,
                chromiumos_overlay=fake_chromiumos_overlay,
            )
        )

    def test_failed_verification(self):
        """Test we can catch a bad patch stack."""
        tempdir = self.make_tempdir()
        svn_revision = 1234

        def _runner(args: _RunnerArgs):
            # Actually run the (failing) verification.
            # This fails because the "upstream" of fetch_head_ref
            # is the same as the translated_sha (which is in
            # the "main" that matches the svn_revision).
            self._set_up_mocking(
                translate_sha=args.main_sha, fetch_head_ref=args.main_sha
            )
            try:
                self.assertFalse(
                    verify_patch_consistency.verify_in_worktree(
                        toolchain_utils_dir=args.toolchain_utils_dir,
                        llvm_src_dir=args.llvm_src_dir,
                        patches_json=args.patches_json,
                        chromiumos_overlay=args.chromiumos_overlay,
                        svn_revision=svn_revision,
                        cl_ref=args.main_sha,
                        ignore_cq_depend=True,
                    )
                )
            finally:
                self._stop_mocking()

        self._run_llvm_harness(tempdir, svn_revision, _runner)

    def test_successful_verification(self):
        """Test we can successfully verify a patch stack."""
        tempdir = self.make_tempdir()
        svn_revision = 1234

        def _runner(args: _RunnerArgs):
            # Actually run the verification. Notably,
            # the difference here between the fail case is the
            # fetch_head_ref is the patch_branch which acts
            # as our "upstream".
            self._set_up_mocking(
                translate_sha=args.main_sha,
                fetch_head_ref=args.patch_branch,
            )
            try:
                self.assertTrue(
                    verify_patch_consistency.verify_in_worktree(
                        toolchain_utils_dir=args.toolchain_utils_dir,
                        llvm_src_dir=args.llvm_src_dir,
                        patches_json=args.patches_json,
                        chromiumos_overlay=args.chromiumos_overlay,
                        svn_revision=svn_revision,
                        cl_ref=args.patch_branch,
                        ignore_cq_depend=True,
                    )
                )
            finally:
                self._stop_mocking()

        self._run_llvm_harness(tempdir, svn_revision, _runner)

    @mock.patch.object(git_utils, "log")
    def test_cq_depend_detection_passes_if_no_cq_depend(self, git_log_mock):
        # Put this in a tempdir in case it escapes the mocks somehow; we know
        # the tempdir is empty.
        tempdir = self.make_tempdir()
        git_log_mock.return_value = textwrap.dedent(
            """\
            foo bar baz
            > Cq-Depend: that doesn't matter
            Cq-Depend that also doesn't matter (no `:`)
            qux
            """
        )
        self.assertTrue(
            verify_patch_consistency.verify_no_cq_depends(
                worktree_dir=tempdir, head="HEAD", baseline_hash="HEAD~"
            )
        )

    @mock.patch.object(git_utils, "log")
    def test_cq_depend_detection_raises_if_no_log_contents(self, git_log_mock):
        tempdir = self.make_tempdir()

        git_log_mock.return_value = ""
        with self.assertRaisesRegex(ValueError, "`git log` was empty.*"):
            verify_patch_consistency.verify_no_cq_depends(
                worktree_dir=tempdir, head="HEAD", baseline_hash="HEAD~"
            )

        git_log_mock.return_value = "\n"
        with self.assertRaisesRegex(ValueError, "`git log` was empty.*"):
            verify_patch_consistency.verify_no_cq_depends(
                worktree_dir=tempdir, head="HEAD", baseline_hash="HEAD~"
            )

    @mock.patch.object(git_utils, "log")
    def test_cq_depend_detection_fails_if_cq_depend(self, git_log_mock):
        tempdir = self.make_tempdir()
        git_log_mock.return_value = textwrap.dedent(
            """\
            foo bar baz
            Cq-Depend: chromium:1234
            qux
            """
        )
        self.assertFalse(
            verify_patch_consistency.verify_no_cq_depends(
                worktree_dir=tempdir, head="HEAD", baseline_hash="HEAD~"
            )
        )
