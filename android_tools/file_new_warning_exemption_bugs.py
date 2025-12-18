# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Creates `bugged` reports for a given warning suppression summary."""

import argparse
import collections
import dataclasses
import itertools
import logging
from pathlib import Path
import re
import shlex
import sys

from android_tools import find_owners
from android_tools import parse_and_apply_warning_exemptions
from cros_utils import bugs


TARGET_DIR_RE = re.compile(r"//([^:]+):")


def get_git_repo_root(known_git_repos: set[Path], target: str) -> Path:
    """Finds the git repo root for a given target.

    Args:
        known_git_repos: A set of git repositories that are edited.
        target: The soong target (e.g., //bionic/libc:libc_bootstrap).

    Returns:
        A path pointing to the git repo that directly contains `target`.
    """
    target_match = TARGET_DIR_RE.match(target)
    if not target_match:
        raise ValueError(f"Target {target} doesn't match {TARGET_DIR_RE}")

    target_path = Path(target_match.group(1))
    # Check candidates from longest to shortest (target_path itself, then
    # parents). The first one found in known_git_repos is the correct one.
    git_repo = next(
        (
            x
            for x in itertools.chain([target_path], target_path.parents)
            if x in known_git_repos
        ),
        None,
    )
    if not git_repo:
        raise ValueError(
            f"Target path {target_path} (from {target}) is not in any known "
            "git repo."
        )
    return git_repo


def convert_target_to_android_bp(target: str) -> Path:
    """Infers an Android.bp file path from a target."""
    target_match = TARGET_DIR_RE.match(target)
    if not target_match:
        raise ValueError(f"Target {target!r} doesn't match {TARGET_DIR_RE}")

    # This match ends up being e.g., `bionic/libc` when given the target
    # `bionic/libc:libc`. `:libc` says "the libc target in the Android.bp
    # existing in `bionic/libc`.
    target_dir = Path(target_match.group(1))
    return target_dir / "Android.bp"


def format_bug_body(
    git_repo_relative_path: Path,
    targets_and_warnings: dict[str, list[str]],
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


@dataclasses.dataclass(frozen=True)
class ProcessTargetsResult:
    """Holds the results of processing targets."""

    # A dict of git repos mapping to targets in that repo. All of these are
    # relative to Android's root.
    targets_by_repo: dict[Path, list[str]]
    # A dict of git repos mapping to Android.bps in the repo. All of these are
    # relative to Android's root.
    android_bp_files_by_repo: dict[Path, list[Path]]


def process_targets(
    known_git_repos: set[Path], targets: list[str]
) -> ProcessTargetsResult:
    """Groups targets by their git repo and finds their Android.bp files."""
    targets_by_repo = collections.defaultdict(list)
    android_bp_files_by_repo = collections.defaultdict(set)

    for target in targets:
        git_repo = get_git_repo_root(known_git_repos, target)
        targets_by_repo[git_repo].append(target)

        android_bp = convert_target_to_android_bp(target)
        android_bp_files_by_repo[git_repo].add(android_bp)

    # Sort for consistency in output.
    for v in targets_by_repo.values():
        v.sort()

    deduped_android_bps = {
        k: sorted(v) for k, v in android_bp_files_by_repo.items()
    }

    return ProcessTargetsResult(
        targets_by_repo=targets_by_repo,
        android_bp_files_by_repo=deduped_android_bps,
    )


def lookup_owners_for_git_repos(
    android_tree: Path, android_bp_files_by_repo: dict[Path, list[Path]]
) -> dict[Path, str]:
    """Looks up OWNERS for the given git repos.

    Args:
        android_tree: Path to the root of an Android repo.
        android_bp_files_by_repo: A mapping of
            `{git_repo_root: list_of_android_bps_owners_are_needed_for}`.
            The `git_repo_root` should be relative to `android_tree`.
    """
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
        repo_cache,
        find_owners.INTERNAL_GERRIT_HOST,
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
    return parser.parse_args(argv)


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

    # Mapping of git repo to warnings observed per target.
    logging.info("Grouping targets by git repo...")
    known_git_repos = set(summary.git_dirs)
    processed_targets = process_targets(
        known_git_repos, list(summary.updated_targets.keys())
    )

    logging.info(
        "Found %d git repos with suppressions.",
        len(processed_targets.targets_by_repo),
    )

    repo_owners = lookup_owners_for_git_repos(
        opts.android_tree, processed_targets.android_bp_files_by_repo
    )

    generated_bugs = []

    for git_repo, targets in sorted(processed_targets.targets_by_repo.items()):
        targets_dict = {t: summary.updated_targets[t] for t in targets}
        body = format_bug_body(
            git_repo_relative_path=git_repo,
            targets_and_warnings=targets_dict,
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
