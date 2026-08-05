# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Verify patch metadata in a commit.

Used by commit hooks in llvm-project. This will verify that commit metadata is
present, and not typoed.
"""

import argparse
import logging
from pathlib import Path
import subprocess

from cros_utils import git_utils
from llvm_tools import patch_utils


CROSTC_WORKER_AUTHOR = (
    "crostc-worker <crostc-worker@crostc-chrotomation.iam.gserviceaccount.com>"
)
LLVM_NO_METADATA_TAG = "LLVM_SKIP_METADATA_CHECKS"
TOP_LEVEL_LOCAL_FILES = frozenset(
    (
        "GEMINI.md",
        "OWNERS",
        "OWNERS.toolchain",
        "PRESUBMIT.cfg",
    )
)


def verify_original_sha(
    llvm_dir: Path,
    original_sha: str,
    remote: str = git_utils.CROS_EXTERNAL_REMOTE,
) -> bool:
    """Verifies that original_sha exists in llvm_dir or upstream remote.

    If not found locally, queries the remote directly via shallow dry-run fetch.
    """
    commit_ref = f"{original_sha}^{{commit}}"
    try:
        git_utils.resolve_ref(llvm_dir, commit_ref, quiet=True)
        return True
    except subprocess.CalledProcessError:
        logging.info(
            "SHA %s not found locally in %s. Querying remote %s...",
            original_sha,
            llvm_dir,
            remote,
        )

    return git_utils.check_remote_if_ref_or_sha_exists(
        llvm_dir, remote, original_sha
    )


def validate_parsed_metadata(
    *,
    parsed: patch_utils.ParsedCommitMetadata,
    commit_meta: git_utils.CommitMetadata,
    original_sha_valid: bool,
    llvm_rev_content: str | None,
    llvm_rev_file_path: Path,
    llvm_dir: Path,
) -> list[str]:
    errors = []
    if parsed.cherry:
        if (
            commit_meta.author != commit_meta.committer
            and CROSTC_WORKER_AUTHOR
            not in (commit_meta.author, commit_meta.committer)
        ):
            errors.append(
                f"patch.cherry is true, but author ({commit_meta.author}) is "
                f"neither the committer ({commit_meta.committer}) nor "
                f"'{CROSTC_WORKER_AUTHOR}'"
            )

    if parsed.original_sha and not original_sha_valid:
        errors.append(
            f"patch.metadata.original_sha '{parsed.original_sha}' "
            f"not found locally in {llvm_dir} or on upstream remote"
        )

    if llvm_rev_content is None:
        errors.append(
            f"No LLVM rev file found at {llvm_rev_file_path}, "
            "are you on a branch with a ChromeOS Base Commit?"
        )
        return errors

    try:
        current_rev = int(llvm_rev_content.strip())
        version_from = parsed.version_from
        if version_from is not None and version_from > current_rev:
            errors.append(
                f"patch.version_range.from ({version_from}) must be "
                f"<= current llvm revision ({current_rev})"
            )

        version_until = parsed.version_until
        if version_until is not None and version_until <= current_rev:
            errors.append(
                f"patch.version_range.until ({version_until}) must be "
                f"> current llvm revision ({current_rev})"
            )
    except ValueError:
        errors.append(f"Invalid integer in {llvm_rev_file_path}")

    return errors


def verify_metadata(
    metadata: dict[str, str],
    llvm_dir: Path,
    git_dir: Path = Path("."),
    commit_ref: str = "HEAD",
) -> list[str]:
    """Verifies the parsed metadata.

    Returns:
        A list of error messages. If empty, metadata is valid.
    """
    try:
        parsed = patch_utils.ParsedCommitMetadata.from_dict(metadata)
    except patch_utils.MetadataValueError as e:
        return e.complaints

    commit_meta = git_utils.get_commit_metadata(git_dir, commit_ref)
    sha_valid = (
        verify_original_sha(llvm_dir, parsed.original_sha)
        if parsed.original_sha
        else True
    )

    llvm_rev_file = llvm_dir / "cros" / "llvm-rev"
    try:
        llvm_rev_content: str | None = llvm_rev_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        llvm_rev_content = None

    return validate_parsed_metadata(
        parsed=parsed,
        commit_meta=commit_meta,
        original_sha_valid=sha_valid,
        llvm_rev_content=llvm_rev_content,
        llvm_rev_file_path=llvm_rev_file,
        llvm_dir=llvm_dir,
    )


def check_skip_metadata_checks(commit_body: str) -> bool:
    """Checks if commit message has LLVM_SKIP_METADATA_CHECKS=<reason>."""
    return any(
        line.startswith(f"{LLVM_NO_METADATA_TAG}=")
        for line in commit_body.splitlines()
    )


def is_local_file(file_path: str) -> bool:
    """Checks if a modified file is local to ChromeOS.

    If all files in a commit are local, no patch metadata (or opt-out) is
    requested.
    """
    parts = Path(file_path).parts
    if not parts:
        return False
    return parts[0] == "cros" or (
        len(parts) == 1 and parts[0] in TOP_LEVEL_LOCAL_FILES
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--git-dir",
        type=Path,
        default=Path("."),
        help="Path to the git repository (default: current directory)",
    )
    parser.add_argument(
        "commit_ref",
        help="Commit reference to verify (e.g., HEAD, a commit SHA)",
    )
    parser.add_argument(
        "--llvm-dir",
        type=Path,
        required=True,
        help="Path to the LLVM repository to verify cherry SHAs",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args(argv)
    if not args.git_dir.is_dir():
        parser.error(f"--git-dir {args.git_dir} is not a directory")

    if not args.llvm_dir.is_dir():
        parser.error(f"--llvm-dir {args.llvm_dir} is not a directory")

    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=log_level,
    )

    commit_body = git_utils.get_commit_message_body(
        args.git_dir, args.commit_ref
    )
    if check_skip_metadata_checks(commit_body):
        logging.info(
            "Skipping metadata checks due to LLVM_SKIP_METADATA_CHECKS"
        )
        return 0

    try:
        metadata = git_utils.parse_message_metadata(commit_body.splitlines())
    except ValueError as e:
        logging.error("Commit metadata error: %s", e)
        logging.error(
            "Put `%s=<reason>` in your commit message "
            "to skip this preupload check",
            LLVM_NO_METADATA_TAG,
        )
        logging.error("Verification failed.")
        return 1

    patch_metadata = {
        k: v for k, v in metadata.items() if k.startswith("patch.")
    }

    if patch_metadata:
        logging.info("Found patch metadata:")
        for k, v in sorted(patch_metadata.items()):
            logging.info("  %s: %s", k, v)

        logging.info("Verifying...")
        errors = verify_metadata(
            patch_metadata,
            llvm_dir=args.llvm_dir,
            git_dir=args.git_dir,
            commit_ref=args.commit_ref,
        )
    else:
        logging.info(
            "No patch metadata found in commit. Checking modified files..."
        )
        modified_files = git_utils.list_files_changed_by_commit(
            args.git_dir, args.commit_ref
        )
        if all(is_local_file(f) for f in modified_files):
            logging.info("All modified files are local.")
            return 0
        errors = ["Commit modifies upstream files but has no patch metadata"]

    if not errors:
        logging.info("Patch metadata (or lack thereof) looks good.")
        return 0

    for err in errors:
        logging.error(err)

    logging.error(
        "Put `%s=<reason>` in your commit message "
        "to skip this preupload check",
        LLVM_NO_METADATA_TAG,
    )
    logging.error("Verification failed.")
    return 1
