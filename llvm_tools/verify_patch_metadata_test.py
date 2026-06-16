# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for verify_patch_metadata."""

from pathlib import Path
import subprocess
from unittest import mock

from cros_utils import git_utils
from llvm_tools import patch_utils
from llvm_tools import test_helpers
from llvm_tools import verify_patch_metadata


class TestVerifyPatchMetadata(test_helpers.TempDirTestCase):
    """Tests for verify_patch_metadata functions."""

    def setUp(self) -> None:
        self.mock_dir = self.make_tempdir()
        cros_dir = self.mock_dir / "cros"
        cros_dir.mkdir()
        (cros_dir / "llvm-rev").write_text("200\n", encoding="utf-8")

    def test_verify_metadata_cherry_author_checks(self) -> None:
        metadata = {
            "patch.cherry": "true",
            "patch.metadata.original_sha": "a" * 40,
        }
        parsed = patch_utils.ParsedCommitMetadata.from_dict(metadata)
        llvm_dir = Path("/fake/llvm")
        rev_path = llvm_dir / "cros" / "llvm-rev"

        # 1. Author == Committer (valid)
        meta = git_utils.CommitMetadata(
            author="Dev <dev@chromium.org>",
            committer="Dev <dev@chromium.org>",
        )
        self.assertEqual(
            verify_patch_metadata.validate_parsed_metadata(
                parsed=parsed,
                commit_meta=meta,
                original_sha_valid=True,
                llvm_rev_content="200\n",
                llvm_rev_file_path=rev_path,
                llvm_dir=llvm_dir,
            ),
            [],
        )

        # 2. Author == crostc-worker (valid)
        meta = git_utils.CommitMetadata(
            author=(
                "crostc-worker "
                "<crostc-worker@crostc-chrotomation.iam.gserviceaccount.com>"
            ),
            committer="Other <other@chromium.org>",
        )
        self.assertEqual(
            verify_patch_metadata.validate_parsed_metadata(
                parsed=parsed,
                commit_meta=meta,
                original_sha_valid=True,
                llvm_rev_content="200\n",
                llvm_rev_file_path=rev_path,
                llvm_dir=llvm_dir,
            ),
            [],
        )

        # 2b. Committer == crostc-worker (valid)
        meta2 = git_utils.CommitMetadata(
            author="Dev <dev@chromium.org>",
            committer=(
                "crostc-worker "
                "<crostc-worker@crostc-chrotomation.iam.gserviceaccount.com>"
            ),
        )
        self.assertEqual(
            verify_patch_metadata.validate_parsed_metadata(
                parsed=parsed,
                commit_meta=meta2,
                original_sha_valid=True,
                llvm_rev_content="200\n",
                llvm_rev_file_path=rev_path,
                llvm_dir=llvm_dir,
            ),
            [],
        )

        # 3. Author != Committer and Author != crostc-worker (invalid)
        meta3 = git_utils.CommitMetadata(
            author="Upstream <up@example.com>",
            committer="Dev <dev@chromium.org>",
        )
        errors = verify_patch_metadata.validate_parsed_metadata(
            parsed=parsed,
            commit_meta=meta3,
            original_sha_valid=True,
            llvm_rev_content="200\n",
            llvm_rev_file_path=rev_path,
            llvm_dir=llvm_dir,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "author (Upstream <up@example.com>) is neither the committer",
            errors[0],
        )

        # 4. patch.cherry == false, Author != Committer (valid)
        parsed_no_cherry = patch_utils.ParsedCommitMetadata.from_dict(
            {"patch.cherry": "false"}
        )
        self.assertEqual(
            verify_patch_metadata.validate_parsed_metadata(
                parsed=parsed_no_cherry,
                commit_meta=meta3,
                original_sha_valid=True,
                llvm_rev_content="200\n",
                llvm_rev_file_path=rev_path,
                llvm_dir=llvm_dir,
            ),
            [],
        )

    def test_verify_metadata_valid(self) -> None:
        valid_metadata = {
            "patch.cherry": "true",
            "patch.version_range.from": "123",
            "patch.version_range.until": "null",
            "patch.metadata.original_sha": "a" * 40,
            "patch.metadata.author": "Test",
            "patch.metadata.info": "Info",
            "patch.platforms": "chromiumos",
        }
        meta = git_utils.CommitMetadata(
            author="Dev <dev@chromium.org>",
            committer="Dev <dev@chromium.org>",
        )
        llvm_dir = Path("/fake/llvm")
        rev_path = llvm_dir / "cros" / "llvm-rev"

        parsed = patch_utils.ParsedCommitMetadata.from_dict(valid_metadata)
        self.assertEqual(
            verify_patch_metadata.validate_parsed_metadata(
                parsed=parsed,
                commit_meta=meta,
                original_sha_valid=True,
                llvm_rev_content="200\n",
                llvm_rev_file_path=rev_path,
                llvm_dir=llvm_dir,
            ),
            [],
        )

        # Test patch.cherry=false
        valid_no_cherry = dict(valid_metadata)
        valid_no_cherry["patch.cherry"] = "false"
        del valid_no_cherry["patch.metadata.original_sha"]
        parsed = patch_utils.ParsedCommitMetadata.from_dict(valid_no_cherry)
        self.assertEqual(
            verify_patch_metadata.validate_parsed_metadata(
                parsed=parsed,
                commit_meta=meta,
                original_sha_valid=True,
                llvm_rev_content="200\n",
                llvm_rev_file_path=rev_path,
                llvm_dir=llvm_dir,
            ),
            [],
        )

        # Test patch.version_range.until=integer
        valid_until = dict(valid_metadata)
        valid_until["patch.version_range.until"] = "456"
        parsed = patch_utils.ParsedCommitMetadata.from_dict(valid_until)
        self.assertEqual(
            verify_patch_metadata.validate_parsed_metadata(
                parsed=parsed,
                commit_meta=meta,
                original_sha_valid=True,
                llvm_rev_content="200\n",
                llvm_rev_file_path=rev_path,
                llvm_dir=llvm_dir,
            ),
            [],
        )

        # Test patch.version_range.until=none (case-insensitive)
        for none_val in ("none", "None", "NULL"):
            valid_none = dict(valid_metadata)
            valid_none["patch.version_range.until"] = none_val
            parsed = patch_utils.ParsedCommitMetadata.from_dict(valid_none)
            self.assertEqual(
                verify_patch_metadata.validate_parsed_metadata(
                    parsed=parsed,
                    commit_meta=meta,
                    original_sha_valid=True,
                    llvm_rev_content="200\n",
                    llvm_rev_file_path=rev_path,
                    llvm_dir=llvm_dir,
                ),
                [],
            )

    def test_verify_metadata_invalid_cherry(self) -> None:
        invalid_metadata = {
            "patch.cherry": "maybe",
        }
        errors = verify_patch_metadata.verify_metadata(
            invalid_metadata, self.mock_dir
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("patch.cherry must be 'true' or 'false'", errors[0])

    def test_verify_metadata_invalid_version_from(self) -> None:
        invalid_metadata = {
            "patch.version_range.from": "abc",
        }
        errors = verify_patch_metadata.verify_metadata(
            invalid_metadata, self.mock_dir
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("patch.version_range.from must be an integer", errors[0])

    def test_verify_metadata_invalid_version_until(self) -> None:
        invalid_metadata = {
            "patch.version_range.until": "abc",
        }
        errors = verify_patch_metadata.verify_metadata(
            invalid_metadata, self.mock_dir
        )
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "patch.version_range.until must be an integer or 'null'/'none'",
            errors[0],
        )

    def test_verify_metadata_invalid_sha(self) -> None:
        # Non-full SHA is now fatal (error)
        invalid_metadata = {
            "patch.cherry": "true",
            "patch.metadata.original_sha": "too_short",
        }
        errors = verify_patch_metadata.verify_metadata(
            invalid_metadata, self.mock_dir
        )
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "patch.metadata.original_sha must be a 40-character hex SHA",
            errors[0],
        )

        # Test non-hex chars
        invalid_metadata["patch.metadata.original_sha"] = "g" * 40
        errors = verify_patch_metadata.verify_metadata(
            invalid_metadata, self.mock_dir
        )
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "patch.metadata.original_sha must be a 40-character hex SHA",
            errors[0],
        )

    def test_verify_metadata_unknown_key(self) -> None:
        metadata = {
            "patch.unknown_key": "val",
        }
        errors = verify_patch_metadata.verify_metadata(metadata, self.mock_dir)
        self.assertEqual(len(errors), 1)
        self.assertIn("Unknown patch metadata key", errors[0])

    @mock.patch.object(git_utils, "resolve_ref", autospec=True)
    @mock.patch.object(git_utils, "fetch", autospec=True)
    def test_verify_original_sha_local_success(
        self, mock_fetch: mock.MagicMock, mock_resolve_ref: mock.MagicMock
    ) -> None:
        mock_resolve_ref.return_value = "a" * 40
        sha = "a" * 40

        self.assertTrue(
            verify_patch_metadata.verify_original_sha(self.mock_dir, sha)
        )
        mock_resolve_ref.assert_called_once_with(
            self.mock_dir, f"{sha}^{{commit}}", quiet=True
        )
        mock_fetch.assert_not_called()

    @mock.patch.object(git_utils, "resolve_ref", autospec=True)
    @mock.patch.object(git_utils, "fetch", autospec=True)
    def test_verify_original_sha_fetch_success(
        self, mock_fetch: mock.MagicMock, mock_resolve_ref: mock.MagicMock
    ) -> None:
        # First call raises error, second succeeds
        mock_resolve_ref.side_effect = [
            subprocess.CalledProcessError(returncode=1, cmd="git rev-parse"),
            "a" * 40,
        ]
        sha = "a" * 40

        self.assertTrue(
            verify_patch_metadata.verify_original_sha(self.mock_dir, sha)
        )
        self.assertEqual(mock_resolve_ref.call_count, 2)
        mock_resolve_ref.assert_has_calls(
            [mock.call(self.mock_dir, f"{sha}^{{commit}}", quiet=True)] * 2
        )
        mock_fetch.assert_called_once_with(self.mock_dir)

    @mock.patch.object(git_utils, "resolve_ref", autospec=True)
    @mock.patch.object(git_utils, "fetch", autospec=True)
    def test_verify_original_sha_resolve_after_fetch_failure(
        self,
        mock_fetch: mock.MagicMock,
        mock_resolve_ref: mock.MagicMock,
    ) -> None:
        # Both resolve_ref calls fail, fetch succeeds
        mock_resolve_ref.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd="git rev-parse"
        )
        sha = "a" * 40

        self.assertFalse(
            verify_patch_metadata.verify_original_sha(self.mock_dir, sha)
        )
        self.assertEqual(mock_resolve_ref.call_count, 2)
        mock_resolve_ref.assert_has_calls(
            [mock.call(self.mock_dir, f"{sha}^{{commit}}", quiet=True)] * 2
        )
        mock_fetch.assert_called_once_with(self.mock_dir)

    @mock.patch.object(git_utils, "get_commit_metadata", autospec=True)
    @mock.patch.object(
        verify_patch_metadata, "verify_original_sha", autospec=True
    )
    def test_verify_metadata_calls_verify_original_sha(
        self,
        mock_verify_original_sha: mock.MagicMock,
        mock_get_commit_metadata: mock.MagicMock,
    ) -> None:
        mock_get_commit_metadata.return_value = git_utils.CommitMetadata(
            author="Dev <dev@chromium.org>",
            committer="Dev <dev@chromium.org>",
        )
        sha = "a" * 40
        metadata = {
            "patch.cherry": "true",
            "patch.metadata.original_sha": sha,
        }

        # Test success case
        mock_verify_original_sha.return_value = True
        errors = verify_patch_metadata.verify_metadata(
            metadata, llvm_dir=self.mock_dir
        )
        self.assertEqual(errors, [])
        mock_verify_original_sha.assert_called_once_with(self.mock_dir, sha)

        # Test failure case
        mock_verify_original_sha.reset_mock()
        mock_verify_original_sha.return_value = False
        errors = verify_patch_metadata.verify_metadata(
            metadata, llvm_dir=self.mock_dir
        )
        self.assertEqual(len(errors), 1)
        self.assertIn(f"not found in {self.mock_dir}", errors[0])
        mock_verify_original_sha.assert_called_once_with(self.mock_dir, sha)

    def test_verify_metadata_llvm_rev(self) -> None:
        llvm_dir = Path("/fake/llvm")
        rev_path = llvm_dir / "cros" / "llvm-rev"
        meta = git_utils.CommitMetadata(
            author="Dev <dev@chromium.org>",
            committer="Dev <dev@chromium.org>",
        )

        # Valid range: from <= 500000 < until
        metadata = {
            "patch.version_range.from": "490000",
            "patch.version_range.until": "510000",
        }
        parsed = patch_utils.ParsedCommitMetadata.from_dict(metadata)
        self.assertEqual(
            verify_patch_metadata.validate_parsed_metadata(
                parsed=parsed,
                commit_meta=meta,
                original_sha_valid=True,
                llvm_rev_content="500000\n",
                llvm_rev_file_path=rev_path,
                llvm_dir=llvm_dir,
            ),
            [],
        )

        # Invalid from: from > 500000
        metadata = {
            "patch.version_range.from": "500010",
        }
        parsed = patch_utils.ParsedCommitMetadata.from_dict(metadata)
        errors = verify_patch_metadata.validate_parsed_metadata(
            parsed=parsed,
            commit_meta=meta,
            original_sha_valid=True,
            llvm_rev_content="500000\n",
            llvm_rev_file_path=rev_path,
            llvm_dir=llvm_dir,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "patch.version_range.from (500010) must be <= current llvm "
            "revision (500000)",
            errors[0],
        )

        # Invalid until: until <= 500000
        metadata = {
            "patch.version_range.until": "500000",
        }
        parsed = patch_utils.ParsedCommitMetadata.from_dict(metadata)
        errors = verify_patch_metadata.validate_parsed_metadata(
            parsed=parsed,
            commit_meta=meta,
            original_sha_valid=True,
            llvm_rev_content="500000\n",
            llvm_rev_file_path=rev_path,
            llvm_dir=llvm_dir,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "patch.version_range.until (500000) must be > current llvm "
            "revision (500000)",
            errors[0],
        )

        # Missing llvm-rev file
        errors = verify_patch_metadata.validate_parsed_metadata(
            parsed=patch_utils.ParsedCommitMetadata.from_dict({}),
            commit_meta=meta,
            original_sha_valid=True,
            llvm_rev_content=None,
            llvm_rev_file_path=rev_path,
            llvm_dir=llvm_dir,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("No LLVM rev file found at", errors[0])

        # Invalid integer in llvm-rev file
        errors = verify_patch_metadata.validate_parsed_metadata(
            parsed=patch_utils.ParsedCommitMetadata.from_dict({}),
            commit_meta=meta,
            original_sha_valid=True,
            llvm_rev_content="invalid\n",
            llvm_rev_file_path=rev_path,
            llvm_dir=llvm_dir,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid integer in", errors[0])

    def test_check_skip_metadata_checks(self) -> None:
        # No skip flag
        self.assertFalse(
            verify_patch_metadata.check_skip_metadata_checks("Commit message")
        )

        # Skip flag in commit body
        self.assertTrue(
            verify_patch_metadata.check_skip_metadata_checks(
                "\nLLVM_SKIP_METADATA_CHECKS=testing skip"
            )
        )

        # Skip flag on a line in the middle
        body = "Subject\n\nLLVM_SKIP_METADATA_CHECKS=some reason\n\nBody"
        self.assertTrue(verify_patch_metadata.check_skip_metadata_checks(body))

        # Skip flag with no reason
        self.assertTrue(
            verify_patch_metadata.check_skip_metadata_checks(
                "\nLLVM_SKIP_METADATA_CHECKS="
            )
        )
        self.assertTrue(
            verify_patch_metadata.check_skip_metadata_checks(
                "\nLLVM_SKIP_METADATA_CHECKS=\n"
            )
        )
        self.assertTrue(
            verify_patch_metadata.check_skip_metadata_checks(
                "\nLLVM_SKIP_METADATA_CHECKS= "
            )
        )

        # Skip flag not at start of line
        self.assertFalse(
            verify_patch_metadata.check_skip_metadata_checks(
                "\n  LLVM_SKIP_METADATA_CHECKS=reason"
            )
        )
        self.assertFalse(
            verify_patch_metadata.check_skip_metadata_checks(
                "\nPrefix LLVM_SKIP_METADATA_CHECKS=reason"
            )
        )

        # Skip flag at the very start of the body (e.g. first line of body
        # returned by %b) is valid.
        self.assertTrue(
            verify_patch_metadata.check_skip_metadata_checks(
                "LLVM_SKIP_METADATA_CHECKS=reason"
            )
        )

    def test_is_local_file(self) -> None:
        self.assertTrue(verify_patch_metadata.is_local_file("cros/README.md"))
        self.assertTrue(verify_patch_metadata.is_local_file("cros/sub/a.txt"))

        self.assertTrue(verify_patch_metadata.is_local_file("OWNERS"))
        self.assertTrue(verify_patch_metadata.is_local_file("OWNERS.toolchain"))
        self.assertTrue(verify_patch_metadata.is_local_file("GEMINI.md"))
        self.assertTrue(verify_patch_metadata.is_local_file("PRESUBMIT.cfg"))
        self.assertFalse(verify_patch_metadata.is_local_file("llvm/OWNERS"))
        self.assertFalse(
            verify_patch_metadata.is_local_file("llvm/CMakeLists.txt")
        )
