# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Creates `bugged` reports for a given warning suppression summary."""

import argparse
import logging
from pathlib import Path
import shlex
import sys
from typing import Iterable, Mapping

from android_tools import android_paths
from android_tools import find_owners
from android_tools import parse_and_apply_warning_exemptions
from android_tools import warning_suppression
from cros_utils import bugs
from cros_utils import gerrit_utils


def format_bug_body(
    git_repo_relative_path: Path,
    targets_and_warnings: dict[str, list[str]],
    cl_link: str | None,
    contact: str,
    original_bug: int,
) -> str:
    """Returns a suitable body for the bug."""
    lines = [
        "Hi! This is a notification that the following warning(s) were "
        f"suppressed in {git_repo_relative_path}. Listed per-target, they "
        "are: ",
        "",
    ]

    for target, warnings in sorted(targets_and_warnings.items()):
        lines += (f"- Target: `{target}`", "")
        for w in sorted(warnings):
            lines.append(f"  - `-W{w}`")
        lines.append("")

    if cl_link:
        lines += (
            "",
            f"Warning suppresions for these were probably landed in {cl_link}.",
        )

    lines += (
        "",
        "These were suppressed due to a global change that introduced them "
        f"(e.g., a toolchain upgrade). b/{original_bug} may have more info. "
        "Compiler warnings are very useful for shifting bugs left, so we "
        "recommend that the warnings are fixed.",
        "",
        "For next steps and FAQs, please see "
        "go/android-llvm-warning-suppression-bug.",
        "",
        "If you have any questions, please don't hesitate to reach out "
        f"to {contact}@!",
    )
    return "\n".join(lines)


def lookup_owners_for_git_repos(
    gerrit_client: gerrit_utils.GerritClient,
    android_tree: Path,
    targets_by_repo: Mapping[Path, Iterable[str]],
) -> dict[Path, str]:
    """Looks up OWNERS for the given git repos.

    Args:
        gerrit_client: GerritClient to perform queries with.
        android_tree: Path to the root of an Android repo.
        targets_by_repo: A mapping of
            `{git_repo_root: list_of_targets_owners_are_needed_for}`.
            The `git_repo_root` should be relative to `android_tree`.
    """
    deduped_android_bps = {}
    for git_repo, targets in targets_by_repo.items():
        deduped_android_bps[git_repo] = {
            warning_suppression.convert_target_to_android_bp(x) for x in targets
        }

    android_bp_files_by_repo = {
        k: sorted(v) for k, v in deduped_android_bps.items()
    }

    logging.info(
        "Looking up OWNERS mappings for %d repo(s)...",
        len(android_bp_files_by_repo),
    )
    repo_cache = find_owners.RepoCache.create_from_manifest(
        android_tree / find_owners.ANDROID_MANIFEST_XML_FROM_ROOT
    )

    check_files = {}
    for git_repo, android_bps in android_bp_files_by_repo.items():
        modified_files = sorted(
            {str(x.relative_to(git_repo)) for x in android_bps}
        )
        check_files[str(git_repo)] = modified_files

    all_results = find_owners.fetch_all_likely_relevant_code_owners(
        gerrit_client,
        repo_cache,
        gerrit_utils.ANDROID_INTERNAL_GERRIT_HOST,
        check_files,
    )
    resolved_owners = {
        Path(k): v for k, v in all_results.items() if v is not None
    }
    logging.info(
        "Successfully resolved %d specific OWNERS; failed to resolve %d",
        len(resolved_owners),
        len(all_results) - len(resolved_owners),
    )
    return resolved_owners


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--android-tree",
        type=Path,
        required=True,
        help="Path to the Android source tree",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging."
    )
    parser.add_argument(
        "--contact",
        type=str,
        required=True,
        help="""
        The username/LDAP of the contact person (e.g. 'gbiv'). This is
        passed to e.g., buganizer, so should not have a trailing `@`, nor a
        trailing `@google.com`.
        """,
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory to output bugspecs in.",
    )
    parser.add_argument(
        "--parent-bug",
        type=int,
        required=True,
        help="""
        Bug number; all bugs that are filed will have this as their parent.
        """,
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        required=True,
        help="The summary file from parse_and_apply_warning_exemptions.py.",
    )
    opts = parser.parse_args(argv)

    android_paths.assert_is_valid_android_tree_root(parser, opts.android_tree)

    return opts


def main(argv: list[str]) -> None:
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    if opts.out_dir.exists() and next(opts.out_dir.iterdir(), None):
        sys.exit(f"Output directory {opts.out_dir} is not empty.")

    opts.out_dir.mkdir(parents=True, exist_ok=True)

    summary = parse_and_apply_warning_exemptions.ExemptionSummary.from_file(
        opts.summary_file
    )

    # Mapping of git repo to Android.bp files.
    logging.info("Grouping targets by git repo...")

    logging.info(
        "Found %d git repos with suppressions.",
        len(summary.exemptions),
    )

    targets_by_repo: dict[Path, Iterable[str]] = {}
    for repo_path_str, repo_summary in summary.exemptions.items():
        all_targets: list[str] = []
        for bp_summary in repo_summary.updated_files.values():
            all_targets.extend(bp_summary.per_target_warnings)
        targets_by_repo[Path(repo_path_str)] = all_targets

    gerrit_client = gerrit_utils.GerritClient.create()
    repo_owners = lookup_owners_for_git_repos(
        gerrit_client,
        opts.android_tree,
        targets_by_repo,
    )

    generated_bugs = []

    for git_repo_str, repo_summary in sorted(summary.exemptions.items()):
        git_repo = Path(git_repo_str)
        targets_and_warnings = {}
        for bp_summary in repo_summary.updated_files.values():
            targets_and_warnings.update(bp_summary.per_target_warnings)

        body = format_bug_body(
            git_repo_relative_path=git_repo,
            targets_and_warnings=targets_and_warnings,
            cl_link=repo_summary.uploaded_cl,
            contact=opts.contact,
            original_bug=summary.bug_number,
        )

        title = f"Toolchain warnings are suppressed in {git_repo}"

        bug_content = bugs.format_bug(
            title=title,
            body=body,
            component=bugs.INTERNAL_ANDROID_COMPONENT,
            assignee=repo_owners.get(git_repo, opts.contact),
            parent=opts.parent_bug,
        )
        generated_bugs.append(bug_content)

    for i, content in enumerate(generated_bugs):
        filename = f"{i}.bugged"
        (opts.out_dir / filename).write_text(content, encoding="utf-8")

    logging.info("Wrote %d bug files to %s", len(generated_bugs), opts.out_dir)
    print(
        "To file these bugs, run:\n"
        f"cd {shlex.quote(str(opts.out_dir))} && for x in *.bugged; do "
        'bugged create --format=markdown < "$x" || break; done'
    )
