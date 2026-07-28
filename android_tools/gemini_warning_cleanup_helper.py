# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Helper script for Gemini-driven warning suppression cleanup.

Supports identifying candidates and cleaning up after Gemini runs.
"""

import argparse
import concurrent.futures
import json
import logging
from pathlib import Path
import subprocess
import threading

from android_tools import android_paths
from android_tools import bp_tools
from android_tools import clean_warning_exemptions_with_gemini
from android_tools import parse_and_apply_warning_exemptions as parse_and_apply
from cros_utils import git_utils

_HUNK_HEADER_RE = clean_warning_exemptions_with_gemini._HUNK_HEADER_RE
DiffFileHeader = clean_warning_exemptions_with_gemini.DiffFileHeader
DiffHunk = clean_warning_exemptions_with_gemini.DiffHunk
remove_blank_lines_from_hunk = (
    clean_warning_exemptions_with_gemini.remove_blank_lines_from_hunk
)
iterate_diff_pieces = (
    clean_warning_exemptions_with_gemini.iterate_diff_pieces
)
remove_blank_lines_from_diff = (
    clean_warning_exemptions_with_gemini.remove_blank_lines_from_diff
)
diff_trivially_has_no_dedupe_potential = (
    clean_warning_exemptions_with_gemini.diff_trivially_has_no_dedupe_potential
)
RunConfig = clean_warning_exemptions_with_gemini.RunConfig
run_bpfmt = clean_warning_exemptions_with_gemini.run_bpfmt
amend_head_if_necessary = (
    clean_warning_exemptions_with_gemini.amend_head_if_necessary
)
upload_changes = clean_warning_exemptions_with_gemini.upload_changes


def cmd_identify_candidates(opts: argparse.Namespace) -> None:
    android_tree: Path = (
        opts.android_tree or android_paths.script_android_checkout_or_exit()
    )

    if opts.only_repo:
        repos = [opts.only_repo]
    else:
        summary_file = parse_and_apply.ExemptionSummary.from_file(
            opts.summary_file
        )
        repos = [Path(x) for x in summary_file.exemptions]

    candidates = []
    for repo in repos:
        repo_path = android_tree / repo
        initial_diff = git_utils.diff(
            git_dir=repo_path,
            ref_start="HEAD~",
        )

        if not diff_trivially_has_no_dedupe_potential(initial_diff):
            candidates.append(str(repo))

    print(json.dumps(candidates))


def run_cleanup_on_repo(config: RunConfig, git_repo: Path) -> bool:
    git_repo_path = config.android_tree / git_repo

    has_unstaged = git_utils.has_discardable_changes(git_repo_path)
    logging.info(
        "Processing repo %s (has unstaged changes: %s)", git_repo, has_unstaged
    )

    full_diff = git_utils.diff(
        git_dir=git_repo_path,
        ref_start="HEAD~",
    )

    cleaned_diff = remove_blank_lines_from_diff(full_diff)
    if full_diff != cleaned_diff:
        logging.info("Cleaning up blank lines in %s...", git_repo)
        git_utils.checkout(git_repo_path, "HEAD~", paths=(".",))
        try:
            git_utils.apply_patch_contents(git_repo_path, cleaned_diff)
        except subprocess.CalledProcessError as e:
            e.add_note(f"Failed applying patch:\n{cleaned_diff}")
            raise

    all_bp_files_with_changes = [
        Path(x)
        for x in git_utils.list_uncommitted_files_changed(git_repo_path)
        if x.endswith("Android.bp")
    ]

    if all_bp_files_with_changes:
        all_bp_files_with_changes.sort()
        logging.info(
            "Running final formatting pass on:%s",
            "".join(f"\n  {git_repo / x}" for x in all_bp_files_with_changes),
        )
        run_bpfmt(config, git_repo_path, all_bp_files_with_changes)

    return amend_head_if_necessary(config, git_repo)


def compute_and_upload_changes(
    *,
    android_tree: Path,
    thread_pool: concurrent.futures.ThreadPoolExecutor,
    only_repo: Path | None,
    repos_to_run_on: list[Path],
    amended_repos: set[Path],
    summary_file: parse_and_apply.ExemptionSummary | None,
) -> list[Path]:
    """Uploads cleanup changes.

    Returns:
        A list of git repos where uploading failed.
    """
    if only_repo:
        repos_to_upload = repos_to_run_on
    else:
        assert summary_file is not None
        repos_to_upload = []
        for repo in repos_to_run_on:
            if repo in amended_repos:
                repos_to_upload.append(repo)
                continue

            exemption = summary_file.exemptions.get(str(repo))
            if exemption and not exemption.uploaded_cl:
                repos_to_upload.append(repo)

    if not repos_to_upload:
        logging.info("No repos need uploading.")
        return []

    return upload_changes(
        android_tree,
        thread_pool,
        repos_to_upload,
    )


def cmd_cleanup_and_upload(opts: argparse.Namespace) -> int:
    android_tree: Path = (
        opts.android_tree or android_paths.script_android_checkout_or_exit()
    )

    bpfmt_path = bp_tools.bpfmt_path(android_tree)
    if not bpfmt_path.exists():
        raise FileNotFoundError(f"No bpfmt found at {bpfmt_path}")

    run_config = RunConfig(
        android_tree=android_tree,
        bpfmt=bpfmt_path,
    )

    summary_file = None
    if opts.only_repo:
        repos_to_run_on = [opts.only_repo]
    else:
        summary_file = parse_and_apply.ExemptionSummary.from_file(
            opts.summary_file
        )
        repos_to_run_on = [Path(x) for x in summary_file.exemptions]

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=opts.jobs
    ) as thread_pool:
        future_to_repo = {
            thread_pool.submit(run_cleanup_on_repo, run_config, repo): repo
            for repo in repos_to_run_on
        }

        amended_repos = []
        exceptions = []

        for future in concurrent.futures.as_completed(future_to_repo):
            repo = future_to_repo[future]
            try:
                amended = future.result()
                if amended:
                    amended_repos.append(repo)
            except Exception as e:
                exceptions.append((repo, e))

        failed_uploads = []
        if opts.upload:
            failed_uploads = compute_and_upload_changes(
                android_tree=android_tree,
                thread_pool=thread_pool,
                only_repo=opts.only_repo,
                repos_to_run_on=repos_to_run_on,
                amended_repos=set(amended_repos),
                summary_file=summary_file,
            )

        for repo, exc in exceptions:
            if isinstance(exc, subprocess.CalledProcessError):
                logging.error(
                    "Exception caught making changes to %s; stdstreams:\n%s",
                    repo,
                    exc.stdout,
                    exc_info=exc,
                )
            else:
                logging.error(
                    "Exception caught making changes to %s",
                    repo,
                    exc_info=exc,
                )

        if failed_uploads:
            logging.error(
                "Uploading failed for repo(s):%s",
                "".join(f"\n- {x}" for x in failed_uploads),
            )

        had_failures = exceptions or failed_uploads
        return 1 if had_failures else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    common_parser.add_argument(
        "--android-tree",
        type=Path,
        help="Android tree to modify.",
    )

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # identify-candidates
    parser_identify = subparsers.add_parser(
        "identify-candidates",
        parents=[common_parser],
        help="Identify repositories with warning deduplication potential.",
    )
    group = parser_identify.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--only-repo",
        type=Path,
        help="Only run on the repository given here.",
    )
    group.add_argument(
        "--summary-file",
        type=Path,
        help="The summary file generated by warning suppression script.",
    )

    # cleanup-and-upload
    parser_cleanup = subparsers.add_parser(
        "cleanup-and-upload",
        parents=[common_parser],
        help="Clean up, format, and upload changes.",
    )
    parser_cleanup.add_argument(
        "--jobs",
        type=int,
        default=8,
        help="Max jobs to run at once.",
    )
    upload_group = parser_cleanup.add_mutually_exclusive_group(required=True)
    upload_group.add_argument(
        "--upload",
        action="store_true",
        help="Run `repo upload` on changed repos.",
    )
    upload_group.add_argument(
        "--no-upload",
        action="store_true",
        help="Do not upload changes to Gerrit.",
    )
    group_cleanup = parser_cleanup.add_mutually_exclusive_group(required=True)
    group_cleanup.add_argument(
        "--only-repo",
        type=Path,
        help="Only run on the repository given here.",
    )
    group_cleanup.add_argument(
        "--summary-file",
        type=Path,
        help="The summary file generated by warning suppression script.",
    )

    opts = parser.parse_args(argv)

    if opts.android_tree:
        android_paths.assert_is_valid_android_tree_root(
            parser, opts.android_tree
        )

    return opts


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    match opts.subcommand:
        case "identify-candidates":
            cmd_identify_candidates(opts)
            return 0
        case "cleanup-and-upload":
            return cmd_cleanup_and_upload(opts)
        case _:
            assert False, f"Unreachable subcommand: {opts.subcommand}"
