# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Uses tooling to clean up warning exemptions for a set of git repos.

This tooling expects that you've recently run
`parse_and_apply_warning_exemptions.py` on the tree in question. Specifically:

1. Tools like `bpfmt` are available.
2. Repos that `parse_and_apply_warning_exemptions` branched and modified are
   still in that branched-and-modified state.
"""

import argparse
import concurrent.futures
import dataclasses
import itertools
import logging
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Iterator

from android_tools import android_paths
from android_tools import bp_tools
from android_tools import parse_and_apply_warning_exemptions as parse_and_apply
from cros_utils import git_utils


_HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)")

# More info on `git diff`'s output can be found here:
# https://git-scm.com/docs/diff-format#generate_patch_text_with_p


@dataclasses.dataclass(frozen=True, eq=True)
class DiffFileHeader:
    """A file header in a diff.

    This is expected to be a produced by `git diff` on a single file, on a repo
    which is not currently undergoing any special git operation (e.g., merging).
    """

    # No special parsing is done, but we _do_ need to treat these file headers
    # specially. Example list contents are:
    # [
    #   "diff --git a/... b/...",
    #   "index ...",
    #   "--- a/foo/bar",
    #   "+++ b/baz/qux",
    # ]
    lines: list[str]

    @staticmethod
    def parse(
        lines: list[str], start_idx: int = 0
    ) -> tuple["DiffFileHeader", int] | None:
        """Tries to parse a file header at `start_idx`.

        Returns:
            A tuple of (file_header, first_line_idx_after_header), or None if
            parsing failed.
        """
        file_header_prefix = "diff --git "
        if not lines[start_idx].startswith(file_header_prefix):
            return None

        # So the number of potential follow-ups here is pretty high.
        # Rather than matching those exactly, just search for a
        # hunk header (which indicates diffs exist in the file), or
        # another file header (which indicates that the file may have just been
        # moved, or something).

        i = start_idx + 1
        while i < len(lines):
            if lines[i].startswith(file_header_prefix) or _HUNK_HEADER_RE.match(
                lines[i]
            ):
                break
            i += 1

        return DiffFileHeader(lines=lines[start_idx:i]), i


@dataclasses.dataclass(frozen=True, eq=True)
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
            elif not hunk_line.startswith("\\ No newline at end of file"):
                raise ValueError(
                    f"Invalid line in diff hunk parsing: {hunk_line}"
                )

        if not (old_lines_read == old_len and new_lines_read == new_len):
            raise ValueError(
                f"Invalid diff hunk; "
                f"read {old_lines_read} old lines, want {old_len}, and "
                f"read {new_lines_read} new lines, want {new_len}."
            )

        assert i is not None, "Zero-length hunk that wasn't caught earlier?"
        if i < len(lines) and lines[i].startswith(
            "\\ No newline at end of file"
        ):
            hunk_lines = lines[hunk_start_idx : i + 1]
            next_idx = i + 1
        else:
            hunk_lines = lines[hunk_start_idx:i]
            next_idx = i

        return (
            DiffHunk(
                header=hunk_header,
                lines=hunk_lines,
                old_start=old_start,
                old_len=old_len,
                new_start=new_start,
                new_len=new_len,
                rest=rest,
            ),
            next_idx,
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


def iterate_diff_pieces(
    git_diff: str,
) -> Iterator[DiffFileHeader | DiffHunk | str]:
    """Yields the 'pieces' of the given diff.

    "Pieces" is, loosely, "either a meaningful, structured part of a diff, or a
    str that represents the current line."

    For example, given a diff containing:

    '''
    Foo bar
    --- a/foo
    +++ b/foo
    @@ -1 +1 @@
       line
    trailing line

    '''

    This will yield the following elements:
    ["Foo bar", "--- a/foo", "+++ b/foo", DiffHunk(...), "trailing line", ""]
    """
    # `splitlines()` will ignore a trailing newline, `split()` will preserve it
    # as an empty string.
    lines = git_diff.split("\n")

    line_idx = 0
    while line_idx < len(lines):
        if parsed_header := DiffFileHeader.parse(lines, line_idx):
            header, line_idx = parsed_header
            yield header
        elif parsed_hunk := DiffHunk.parse(lines, line_idx):
            hunk, line_idx = parsed_hunk
            yield hunk
        else:
            yield lines[line_idx]
            line_idx += 1


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
    output_lines = []

    # Defer adding diff file headers, since git isn't fond of file headers with
    # no corresponding diff.
    pending_file_header = None
    pending_header_had_hunk = False

    for piece in iterate_diff_pieces(git_diff):
        if isinstance(piece, DiffFileHeader):
            # If there were no hunks associated with the pending header
            # originally, emit it now.
            if pending_file_header and not pending_header_had_hunk:
                output_lines.extend(pending_file_header.lines)

            pending_file_header = piece
            pending_header_had_hunk = False
            continue

        if isinstance(piece, DiffHunk):
            pending_header_had_hunk = True
            if new_hunk := remove_blank_lines_from_hunk(piece):
                if pending_file_header:
                    output_lines += pending_file_header.lines
                    pending_file_header = None

                output_lines.extend(new_hunk)
            continue

        output_lines.append(piece)

    if pending_file_header and not pending_header_had_hunk:
        output_lines.extend(pending_file_header.lines)

    return "\n".join(output_lines)


@dataclasses.dataclass(frozen=True)
class RunConfig:
    """Configuration for a cleanup run."""

    android_tree: Path
    bpfmt: Path


_DASH_W_NO_STRING_RE = re.compile(r'"(-Wno-[^"]+)"')


def diff_trivially_has_no_dedupe_potential(file_diff: str) -> bool:
    """Returns True if `file_diff` has no dedupe potential.

    This is meant to be a quick heuristic that lets us skip Gemini in cases
    where it's obviously not needed. The simple act of starting Gemini and
    sending a query for it to respond "it's obvious that no changes are needed,"
    takes seconds of wall-time. Taking a few milliseconds instead speeds things
    up dramatically in the common case that a file has just one change.
    """
    dash_w_strings = set()
    for piece in iterate_diff_pieces(file_diff):
        if not isinstance(piece, DiffHunk):
            continue

        for line in piece.lines:
            if not line.startswith("+"):
                continue

            for m in _DASH_W_NO_STRING_RE.findall(line):
                # If we've already found this `-Wno-` string elsewhere in the
                # diff, there's room for it to be deduped.
                if m in dash_w_strings:
                    return False
                dash_w_strings.add(m)

    return True


def run_bpfmt(
    config: RunConfig,
    git_repo_path: Path,
    files: list[Path],
) -> None:
    try:
        subprocess.run(
            (config.bpfmt, "-w", *files),
            cwd=git_repo_path,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as e:
        e.add_note(f"Formatting failed on {files}")
        raise


def amend_head_if_necessary(run_config: RunConfig, git_repo: Path) -> bool:
    """Amends HEAD if changes are present; returns True if amended."""
    git_repo_path = run_config.android_tree / git_repo
    if not git_utils.has_discardable_changes(git_repo_path):
        logging.debug("No changes made to %s; not amending", git_repo_path)
        return False

    logging.debug("Changes made to %s; amending", git_repo_path)
    git_utils.amend_head_with_all_changes(git_repo_path, quiet=True)
    return True


def upload_changes(
    android_tree: Path,
    thread_pool: concurrent.futures.ThreadPoolExecutor,
    repos_to_upload: list[Path],
) -> list[Path]:
    """Uploads all changes.

    Returns:
        A list of git repos where uploading failed.
    """
    # Bound this heavily, since gerrit may rate-limit a significant number of
    # requests.
    upload_semaphore = threading.BoundedSemaphore(2)

    def upload_one_repo(repo_subpath: Path) -> int:
        repo = android_tree / repo_subpath
        with upload_semaphore:
            cls_uploaded = git_utils.upload_to_gerrit(
                git_repo=repo,
                remote=git_utils.ANDROID_INTERNAL_REMOTE,
                branch=git_utils.ANDROID_MAIN_BRANCH,
                # Assume this has already been uploaded with the correct topic
                # and `wip` state.
            )

        if not cls_uploaded:
            raise ValueError(f"Unexpected: no CLs uploaded in {repo}")

        if len(cls_uploaded) != 1:
            logging.warning(
                "Uploaded %d CLs in %s somehow - ignoring all but the last",
                len(cls_uploaded),
                repo,
            )
        return cls_uploaded[-1]

    logging.info("Uploading %d CLs...", len(repos_to_upload))

    # Don't use `map`, since we filter on exceptions later.
    upload_futures = [
        thread_pool.submit(upload_one_repo, x) for x in repos_to_upload
    ]

    exceptions = []
    for repo, upload_result in zip(repos_to_upload, upload_futures):
        if e := upload_result.exception():
            exceptions.append((repo, e))

    # List exceptions after all threads are done executing for clarity.
    for repo, e in exceptions:
        logging.error(
            "Exception caught uploading changes to %s", repo, exc_info=e
        )

    return [x for x, _ in exceptions]
