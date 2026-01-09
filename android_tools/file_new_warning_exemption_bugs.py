# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Creates `bugged` reports for a given warning suppression summary."""

import argparse
import collections
import itertools
import logging
from pathlib import Path
import re
import shlex
import sys

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


def format_bug_body(
    git_repo_relative_path: Path,
    targets_and_warnings: dict[str, list[str]],
    contact: str,
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
        "(e.g., a toolchain upgrade). Compiler warnings are very useful for "
        "shifting bugs left, so we recommend that the warnings are fixed.",
        "",
        "For next steps and FAQs, please see "
        "go/android-llvm-warning-suppression-bug.",
        "",
        "If you have any questions, please don't hesitate to reach out "
        f"to {contact}@!",
    )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    repo_to_targets: dict[Path, dict[str, list[str]]] = collections.defaultdict(
        dict
    )

    logging.info("Grouping targets by git repo...")
    known_git_repos = set(summary.git_dirs)
    for target, warnings in summary.updated_targets.items():
        git_repo = get_git_repo_root(known_git_repos, target)
        repo_to_targets[git_repo][target] = warnings

    logging.info("Found %d git repos with suppressions.", len(repo_to_targets))

    generated_bugs = []

    for git_repo, targets_dict in sorted(repo_to_targets.items()):
        body = format_bug_body(
            git_repo_relative_path=git_repo,
            targets_and_warnings=targets_dict,
            contact=opts.contact,
        )

        title = f"Toolchain warnings are suppressed in {git_repo}"

        bug_content = bugs.format_bug(
            title=title,
            body=body,
            component=bugs.INTERNAL_ANDROID_COMPONENT,
            # TODO(b/467371906): use code OWNERS location functionality. Doing
            # that mapping adds some complexity, so just set to `opts.contact`
            # for now to keep reviews smaller.
            assignee=opts.contact,
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
