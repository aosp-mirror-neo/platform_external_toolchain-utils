# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Uses tooling to clean up warning exemptions for a set of git repos."""

import argparse
import concurrent.futures
import dataclasses
import logging
from pathlib import Path
import re
import subprocess
import sys

from android_tools import android_paths
from android_tools import parse_and_apply_warning_exemptions as parse_and_apply
from cros_utils import git_utils


_HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)")


@dataclasses.dataclass(frozen=True)
class DiffHunk:
    """A single hunk in a diff.

    This is expected to be a hunk diff produced by `git diff` on a single file,
    on a repo which is not currently undergoing any special git operation (e.g.,
    merging).
    """

    header: str
    lines: list[str]
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    rest: str

    @staticmethod
    def parse(
        lines: list[str], start_idx: int = 0
    ) -> tuple["DiffHunk", int] | None:
        """Tries to parse a hunk at `start_line_idx`.

        Returns:
            A tuple of (hunk, first_line_idx_after_hunk), or None if parsing
            failed.
        """
        line = lines[start_idx]
        match = _HUNK_HEADER_RE.match(line)
        if not match:
            return None

        hunk_header = line
        (
            old_start_str,
            old_len_str,
            new_start_str,
            new_len_str,
            rest,
        ) = match.groups()

        old_start = int(old_start_str)
        new_start = int(new_start_str)
        # Note that if these are omitted, their value is implicitly 1.
        old_len = int(old_len_str) if old_len_str else 1
        new_len = int(new_len_str) if new_len_str else 1

        hunk_start_idx = start_idx + 1
        old_lines_read = 0
        new_lines_read = 0

        if not (old_len or new_len):
            raise ValueError("0-length hunk is nonsensical")

        i = None
        for i in range(hunk_start_idx, len(lines)):
            # We verify that the hunk header was well-formed after this loop, so
            # `>=` here is fine.
            if old_lines_read >= old_len and new_lines_read >= new_len:
                break

            hunk_line = lines[i]
            if hunk_line.startswith("+"):
                new_lines_read += 1
            elif hunk_line.startswith("-"):
                old_lines_read += 1
            elif hunk_line.startswith(" ") or not hunk_line:
                old_lines_read += 1
                new_lines_read += 1
            elif hunk_line.startswith("\\ No newline at end of file"):
                break
            else:
                raise ValueError(
                    f"Invalid line in diff hunk parsing: {hunk_line}"
                )

        if not (old_lines_read == old_len and new_lines_read == new_len):
            raise ValueError(
                f"Invalid diff hunk; "
                f"read {old_lines_read}, old lines, want {old_len}, and "
                f"read {new_lines_read}, old lines, want {new_len}."
            )

        # Checked for a zero-length hunk above, so the loop must have iterated.
        assert i, "Zero-length hunk that wasn't caught earlier?"
        return (
            DiffHunk(
                header=hunk_header,
                lines=lines[hunk_start_idx:i],
                old_start=old_start,
                old_len=old_len,
                new_start=new_start,
                new_len=new_len,
                rest=rest,
            ),
            i,
        )


def remove_blank_lines_from_hunk(hunk: DiffHunk) -> list[str] | None:
    """Per-hunk version of remove_blank_lines_from_diff.

    Returns:
        A new hunk with whitespace removed. If the hunk only consisted of
        `isspace()` additions, returns None.
    """
    new_hunk_lines = []
    removed_count = 0
    for hunk_line in hunk.lines:
        if hunk_line.startswith("+") and not hunk_line[1:].rstrip():
            removed_count += 1
            continue

        new_hunk_lines.append(hunk_line)

    if not any(x.startswith("+") or x.startswith("-") for x in new_hunk_lines):
        return None

    # _Technically_ we should be updating 'new' line numbers in headers if a
    # prior hunk is modified, but git is happy to fuzz.
    if not removed_count:
        return [hunk.header] + hunk.lines

    new_len_updated = hunk.new_len - removed_count

    old_range = f"-{hunk.old_start},{hunk.old_len}"
    new_range = f"+{hunk.new_start},{new_len_updated}"
    new_hunk_header = f"@@ {old_range} {new_range} @@{hunk.rest}"
    new_hunk_lines.insert(0, new_hunk_header)
    return new_hunk_lines


def remove_blank_lines_from_diff(git_diff: str) -> str:
    """This function removes blank lines added by `git_diff`.

    Sometimes, Soong tooling will lead to weird constructs that can't be
    autoformatted away. For example, a diff might contain the following hunk
    (ignoring the header):

    ```
     cc_defaults {
       name: "foo",
    +  cflags: ["-Wbar"],
    +
     }
    ```

    This modifies the given diff to remove the empty added line. It does nothing
    about removed or unchanged lines.
    """
    lines = git_diff.split("\n")
    output_lines = []
    line_idx = 0
    while line_idx < len(lines):
        if parsed_hunk := DiffHunk.parse(lines, line_idx):
            hunk, line_idx = parsed_hunk
            if new_hunk := remove_blank_lines_from_hunk(hunk):
                output_lines.extend(new_hunk)
        else:
            output_lines.append(lines[line_idx])
            line_idx += 1

    return "\n".join(output_lines)


@dataclasses.dataclass(frozen=True)
class RunConfig:
    """Shared attributes between clean-up runs."""

    android_tree: Path
    gemini_prompt: str


def read_gemini_prompt() -> str:
    prompt_md = (
        android_paths.script_toolchain_utils_root()
        / "android_tools"
        / "clean_warning_exemptions_prompt.md"
    )
    return prompt_md.read_text(encoding="utf-8")


def run_on_repo(config: RunConfig, git_repo: Path) -> bool:
    """Runs on a git repo, returning True if changes were made."""

    logging.info("Running Gemini on %s...", git_repo)
    git_repo_path = config.android_tree / git_repo

    head_contents = git_utils.format_patch(git_repo_path, "HEAD")
    # Use gemini-cli rather than Gemini's API, since gemini-cli has the built-in
    # ability to edit files/etc.
    run_result = subprocess.run(
        (
            "gemini",
            # This isn't an interactive session; approve all edits, but stop
            # short of running anything else.
            "--approval-mode=auto_edit",
            "\n".join((config.gemini_prompt, head_contents)),
        ),
        cwd=git_repo_path,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )

    if run_result.returncode:
        logging.error(
            "Gemini failed on %s; stdout/stderr:\n%s",
            git_repo,
            run_result.stdout,
        )
        run_result.check_returncode()

    logging.debug("Gemini's output on %s was:\n%s", git_repo, run_result.stdout)

    # Gemini is instructed to only make changes to files, and has no ability to
    # use tools like `git`. So if there's a diff, it made changes.
    if git_utils.has_discardable_changes(git_repo_path):
        new_sha = git_utils.amend_head_with_all_changes(
            git_repo_path, quiet=True
        )
        logging.info(
            "Committed Gemini's changes to %s; new HEAD is %s.",
            git_repo,
            new_sha,
        )
        gemini_made_change = True
    else:
        gemini_made_change = False

    # Now, clean up any blank lines that Gemini may have added.
    head_patch = git_utils.format_patch(git_repo_path, "HEAD")
    cleaned_patch = remove_blank_lines_from_diff(head_patch)
    if head_patch == cleaned_patch:
        return gemini_made_change

    logging.info("Removing blank lines from CL in %s", git_repo)
    git_utils.checkout(git_repo_path, "HEAD~", paths=(".",))
    try:
        git_utils.apply_patch_contents(git_repo_path, cleaned_patch)
    except subprocess.CalledProcessError:
        logging.error("Failed applying patch:\n%s", cleaned_patch)
        raise
    new_sha = git_utils.amend_head_with_all_changes(git_repo_path, quiet=True)
    logging.info(
        "Re-committed Gemini's changes to %s (no blank lines); new HEAD is %s.",
        git_repo,
        new_sha,
    )
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--android-tree",
        type=Path,
        help="""
        Android tree to modify. If not specified, autodetection from this
        script's directory will be attempted.
        """,
    )
    parser.add_argument(
        "--jobs", type=int, default=8, help="Max jobs to run at once."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--only-repo",
        type=Path,
        help="""
        Only run on the repository given here. This repository should be
        relative to the Android root, e.g., `bionic/`.
        """,
    )
    group.add_argument(
        "--summary-file",
        type=Path,
        help="""
        The --update-summary-file generated by the warning suppression script.
        """,
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    android_tree: Path = (
        opts.android_tree or android_paths.script_android_checkout_or_exit()
    )
    summary_file_path: Path = opts.summary_file
    only_repo: Path | None = opts.only_repo

    if only_repo:
        only_repo_git_dir = android_tree / only_repo / ".git"
        if not only_repo_git_dir.exists():
            sys.exit(
                f"Path at {only_repo_git_dir} doesn't exist; check that "
                "`--only-repo`'s value is correct."
            )

        repos_to_run_on = [only_repo]
    else:
        summary_file = parse_and_apply.ExemptionSummary.from_file(
            summary_file_path
        )
        repos_to_run_on = summary_file.git_dirs

    run_config = RunConfig(
        android_tree=android_tree,
        gemini_prompt=read_gemini_prompt(),
    )

    with concurrent.futures.ThreadPoolExecutor(opts.jobs) as thread_pool:
        futures = []
        for r in repos_to_run_on:
            f = thread_pool.submit(
                run_on_repo,
                run_config,
                r,
            )
            futures.append((r, f))

        exceptions = []
        num_successful_updates = 0
        for repo, run_result in futures:
            if e := run_result.exception():
                exceptions.append((repo, e))
            elif run_result.result():
                logging.info("Successfully updated %s", repo)
                num_successful_updates += 1

        # Log exceptions in a batch afterward, so they don't get lost in any
        # logging output above.
        for repo, e in exceptions:
            logging.error(
                "Exception caught making changes to %s", repo, exc_info=e
            )

        logging.info("Applied updates to %d repos", num_successful_updates)
        sys.exit(1 if exceptions else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
